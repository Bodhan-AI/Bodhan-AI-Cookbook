"""Regression tests for the independent side audit of commit 518ed85 (finding IDs in the
test names: S = specification, N = deep review, E = engineering, F = boundary review).
Each test reproduces the reported scenario with synthetic data and the offline fakes."""

import asyncio
import io
import json
import time

import pytest
from conftest import make_image, speechlike, write_wav
from fakes import FakeBodhan, ScriptedChat, wav_tone
from test_agent_e2e import args, last_ok, read_outputs

from bodhan_agent.cli import UsageError, run
from bodhan_agent.llm import PlannerLLM
from bodhan_mcp import errors as E
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import BodhanKeys
from bodhan_mcp.languages import detect_language, normalize_language
from bodhan_mcp.registry import invoke
from bodhan_mcp.runtime import CostLedger, ResultCache, RunContext
from bodhan_mcp.tools import TOOL_BY_NAME

KEYS = BodhanKeys("k1", "k2", "k3", "k4")


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


async def call(ctx, tool_name, **a):
    return await invoke(TOOL_BY_NAME[tool_name], ctx, a)


def ctx_with_cache(tmp_path, fake, cache_dir, max_inr=None):
    ledger = CostLedger(max_inr=max_inr)
    cache = ResultCache(cache_dir)
    client = BodhanClient(KEYS, ledger, cache, transport=fake.transport, backoff_base=0.0, timeout=5)
    out = tmp_path / "art"
    out.mkdir(exist_ok=True)
    return RunContext(artifacts_dir=out, ledger=ledger, cache=cache, client=client)


# ---- S01: a failed translation cannot be relabelled by re-checking it against itself ----------
async def test_S01_failed_translation_cannot_be_reverified_against_itself(make_ctx, tmp_path):
    fake = FakeBodhan(drop_last_sentence=True)
    src = tmp_path / "src.txt"
    src.write_text("The library opens at eight. Volunteers added many new books for the children.", encoding="utf-8")
    ctx = make_ctx(fake)
    r, err = await call(ctx, "translate_text", path=str(src), target_language="hi")
    assert not err and r["verification"]["status"] == "fail"
    v, err = await call(ctx, "verify_translation", translation_path=r["path"], source_path=r["path"], target_language="hi", source_language="hi")
    assert err and v["error"]["code"] == "invalid_argument"
    v, err = await call(ctx, "verify_translation", translation_path=r["path"], source_path=str(src), target_language="hi")
    assert ctx.artifacts[r["path"]].verification["status"] == "fail"  # history kept, never upgraded
    assert len(ctx.artifacts[r["path"]].history) >= 2


# ---- S02: an unrelated artifact cannot satisfy a declared audio deliverable ---------------------
async def test_S02_unrelated_note_does_not_complete_an_audio_task(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("A paragraph about trees.", encoding="utf-8")
    claim = lambda m: [("finish", {"status": "completed", "final_answer": "Created the requested Tamil MP3.", "deliverables": [last_ok(m, "path")]})]
    chat = ScriptedChat([[("record_plan", {"steps": ["speak"], "deliverables": [{"kind": "audio", "language": "ta"}]})],
                         [("write_text", {"name": "note.md", "content": "An unrelated English note."})], claim, claim, claim])
    await run(args(tmp_path, "Produce a Tamil MP3 of this paragraph", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] != "completed" and not any(d["kind"] == "audio" for d in rep["deliverables"])
    assert any("declared deliverable 'audio (ta)" in str(e.get("reasons")) for e in trace if e["type"] == "finish_rejected")


# ---- N01: character references cannot smuggle planner-written Indic text -----------------------
async def test_N01_html_character_references_are_checked_as_rendered(make_ctx):
    ctx = make_ctx(FakeBodhan())
    for content in ("Summary: &#2351;&#2361; &#x92E;&#x948;&#x902;", "<p>&#2351;&#2361;</p>", "&amp;#2351;"):
        r, err = await call(ctx, "write_text", name="x.md", content=content)
        assert err and r["error"]["code"] == "provenance_violation", content
    r, err = await call(ctx, "write_text", name="ok.md", content="Fish &amp; chips cost 5 &lt; 10.")
    assert not err


async def test_N01_final_answer_entities_are_withheld(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees give shade.", encoding="utf-8")
    sneaky = "Answer: &#2346;&#2375;&#2337;"
    chat = ScriptedChat([[("record_plan", {"steps": ["x"], "deliverables": [{"kind": "text", "language": "en"}]})],
                         [("write_text", {"name": "a.md", "content": "Trees give shade."})]]
                        + [lambda m: [("finish", {"status": "completed", "final_answer": sneaky, "deliverables": [last_ok(m, "path")]})]] * 3)
    await run(args(tmp_path, "Explain", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert sneaky not in rep["final_answer"] and "&#2346;" not in md


# ---- N02: malformed control calls never unlock paid tools or crash the run --------------------
async def test_N02_malformed_controls_are_rejected_without_side_effects(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees give shade.", encoding="utf-8")

    class Raw(ScriptedChat):
        async def create(self, **kw):
            resp = await super().create(**kw)
            for c in resp.choices[0].message.tool_calls or []:
                if c.function.name == "record_plan" and "BROKEN" in c.function.arguments:
                    c.function.arguments = "{"
            return resp

    chat = Raw([[("record_plan", {"steps": ["BROKEN"]})],
                [("translate_text", {"path": str(txt), "target_language": "hi"})],
                [("finish", {"status": "failed", "final_answer": "x", "assumptions": 1})],
                [("finish", {"status": "failed", "final_answer": "Stopping."})]])
    await run(args(tmp_path, "Translate", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    errors = [s["error"] for s in rep["steps"]["tool_calls"] if s["error"]]
    assert errors == ["plan_required"]  # the broken plan did not unlock the paid tool
    assert rep["stop_reason"] == "finished" and rep["status"] == "failed"
    assert sum(e["type"] == "control_rejected" for e in trace) == 2


# ---- N03: a concat manifest disguised as audio cannot read a sibling file ----------------------
async def test_N03_nested_media_reference_is_refused(make_ctx, tmp_path):
    inside, outside = tmp_path / "in", tmp_path / "private"
    inside.mkdir()
    outside.mkdir()
    write_wav(outside / "secret.wav", speechlike(3, 16000))
    (inside / "note.wav").write_text(f"ffconcat version 1.0\nfile '{(outside / 'secret.wav').as_posix()}'\n", encoding="utf-8")
    fake = FakeBodhan()
    r, err = await call(make_ctx(fake, read_roots=[inside]), "transcribe_audio", path=str(inside / "note.wav"))
    assert err and r["error"]["code"] == "unsupported_file_type" and fake.calls["transcriptions"] == 0


# ---- N04: a clean text header does not hide a scanned body -----------------------------------
async def test_N04_pages_with_large_images_are_ocred(make_ctx, tmp_path, monkeypatch):
    from conftest import make_pdf

    import bodhan_mcp.tools.doc_tools as dt

    pdf = make_pdf(tmp_path / "hybrid.pdf", 1)
    header = "This school worksheet contains the complete questions for the examination."
    monkeypatch.setattr(dt, "pdf_text_layer", lambda p, i: header)
    for share, expected_ocr in ((0.6, 1), (0.02, 0)):
        monkeypatch.setattr(dt, "pdf_raster_share", lambda p, i, s=share: s)
        fake = FakeBodhan(ocr_pages=["Header and the full scanned body text."])
        r, err = await call(make_ctx(fake), "ocr_document", path=str(pdf))
        assert not err and fake.calls["ocr"] == expected_ocr, share


# ---- S03 / F04: languages ---------------------------------------------------------------------
async def test_S03_romanized_indic_is_not_kept_as_english(make_ctx):
    fake = FakeBodhan()
    r, err = await call(make_ctx(fake), "translate_text", text="namaste mera naam ravi hai", target_language="en")
    assert not err and fake.calls["translate"] >= 1
    assert detect_language("namaste mera naam ravi hai").language is None
    assert detect_language("The meeting is on Friday and the room is booked.").language == "en"


def test_F04_script_tags_are_case_insensitive_and_never_silently_swapped():
    assert normalize_language("ks-deva") == "ks-Deva" and normalize_language("MNI-BENG") == "mni-Beng"
    assert normalize_language("sd-arab") == "sd-Arab" and normalize_language("hi" + "-IN") == "hi"
    assert normalize_language("ks-latn") == "ks-latn"  # unknown script stays unknown and is rejected later


async def test_S10_explicit_unsupported_source_costs_nothing(make_ctx):
    fake = FakeBodhan()
    r, err = await call(make_ctx(fake), "translate_text", text="Bonjour tout le monde.", target_language="hi", source_language="fr")
    assert err and r["error"]["code"] == "unsupported_language" and fake.calls["translate"] == 0


# ---- S05: step cap counts every call, including controls --------------------------------------
async def test_S05_many_control_calls_cannot_exceed_max_steps(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["x"], "deliverables": [{"kind": "answer"}]})
    chat = ScriptedChat([[plan] * 10] + [[("detect_language", {"path": str(txt)})]] * 5)
    await run(args(tmp_path, "Do it", [txt], max_steps=3), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["steps"]["used"] <= 3 and sum(e["type"] == "plan" for e in trace) <= 3


def test_S05_cli_rejects_nonpositive_limits():
    from bodhan_agent.cli import parse_args

    for bad in (["--max-steps", "0"], ["--max-steps", "-2"], ["--max-seconds", "0"], ["--max-cost-inr", "-1"], ["--max-seconds", "nan"]):
        with pytest.raises(SystemExit):
            parse_args(["--task", "t", "--out", "o", *bad])


# ---- S06: forced stops use the same usable-output rule as finish ------------------------------
async def test_S06_forced_stop_does_not_present_wrappers_of_failed_artifacts(tmp_path):
    img = make_image(tmp_path / "page.png")
    fake = FakeBodhan(ocr_truncate=True, ocr_pages=["partial text"])
    chat = ScriptedChat([[("record_plan", {"steps": ["ocr", "wrap"], "deliverables": [{"kind": "text", "language": "en"}]})],
                         [("ocr_document", {"path": str(img)})],
                         lambda m: [("write_text", {"name": "wrap.md", "content": "Result:\n\n{{artifact:" + last_ok(m, "path") + "}}"})],
                         [("detect_language", {"text": "hello"})]])
    await run(args(tmp_path, "Read this page", [img], max_steps=4), transport=fake.transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["stop_reason"] == "step_limit"
    assert not any(d["path"].endswith("wrap.md") for d in rep["deliverables"])
    assert any("wrap.md" in x for x in rep["limitations"])


# ---- S07 / S08: one deadline; timed-out steps keep their cost --------------------------------
async def test_S07_preflight_is_inside_the_deadline(tmp_path, monkeypatch):
    import bodhan_agent.loop as loop

    async def slow(self, guard):
        await asyncio.sleep(2)

    monkeypatch.setattr(loop.Agent, "_preflight", slow)
    txt = tmp_path / "p.txt"
    txt.write_text("x", encoding="utf-8")
    t0 = time.monotonic()
    await run(args(tmp_path, "Do it", [txt], max_seconds=0.2), transport=FakeBodhan().transport, llm=PlannerLLM(client=ScriptedChat([]), model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["stop_reason"] == "time_limit" and time.monotonic() - t0 < 1.5


async def test_S08_timed_out_step_reports_its_uncertain_cost(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("A sentence to translate slowly.", encoding="utf-8")
    fake = FakeBodhan(delay=3.0)
    chat = ScriptedChat([[("record_plan", {"steps": ["t"], "deliverables": [{"kind": "translation", "language": "hi"}]})],
                         [("translate_text", {"path": str(txt), "target_language": "hi"})]])
    await run(args(tmp_path, "Translate", [txt], max_seconds=1.0), transport=fake.transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    step = next(s for s in rep["steps"]["tool_calls"] if s["tool"] == "translate_text")
    assert step["error"] == "time_limit" and step["cost_inr"] > 0
    assert step["cost_inr"] == pytest.approx(rep["cost"]["bodhan_uncertain_inr"], abs=1e-4)


# ---- S09 / S11 / E02 / E03: documents and limits ---------------------------------------------
async def test_S09_multipage_tiff_pages_are_all_read(make_ctx, tmp_path):
    from PIL import Image

    pages = [Image.new("RGB", (400, 300), c) for c in ("white", "gray")]
    tif = tmp_path / "doc.tiff"
    pages[0].save(tif, save_all=True, append_images=pages[1:])
    fake = FakeBodhan(ocr_pages=["page one", "page two"])
    r, err = await call(make_ctx(fake), "ocr_document", path=str(tif))
    assert not err and fake.calls["ocr"] == 2 and r["pages"] == [1, 2]


async def test_S11_oversized_inputs_are_split_before_sending(make_ctx):
    fake = FakeBodhan()
    long_cell = "word " * 400
    table = "| A | B |\n| --- | --- |\n" + "\n".join(f"| {long_cell[:200]} {i} | {i} |" for i in range(12))
    r, err = await call(make_ctx(fake), "translate_text", text=table, target_language="hi", verify=False)
    assert not err and max(len(q["text"]) for q in fake.requests if q["kind"] == "translate") <= 1500
    r, err = await call(make_ctx(fake), "translate_text", text="x" * 10 + " " + ("y" * 50 + " ") * 120, target_language="hi", verify=False)
    assert not err and max(len(q["text"]) for q in fake.requests if q["kind"] == "translate") <= 3000


def test_E02_huge_page_ranges_are_clipped_before_expansion():
    from bodhan_mcp.documents import parse_page_spec

    t0 = time.monotonic()
    assert parse_page_spec("1-1000000000", 2) == [1, 2]
    assert time.monotonic() - t0 < 0.5
    with pytest.raises(E.ToolError):
        parse_page_spec("1," * 200, 5)


def test_E03_classification_reads_only_a_bounded_header(tmp_path, monkeypatch):
    from pathlib import Path

    from bodhan_mcp.documents import classify

    blob = tmp_path / "noext"
    blob.write_bytes(b"%PDF-1.7" + b"\x00" * 1000)

    def boom(self):
        raise AssertionError("classify must not read whole files")

    monkeypatch.setattr(Path, "read_bytes", boom)
    monkeypatch.setattr(Path, "read_text", boom)
    assert classify(blob) == "pdf"


# ---- N05 / N06: numbers -----------------------------------------------------------------------
async def test_N05_omitted_numbers_cannot_pass_speech_checks(make_ctx):
    fake = FakeBodhan(asr_override="your code is")
    r, err = await call(make_ctx(fake), "synthesize_speech", text="Your code is 123456.", language="en", format="wav")
    assert not err and r["verification"]["status"] == "warn"


async def test_N06_added_numbers_warn_and_swapped_table_values_fail(make_ctx):
    from bodhan_mcp.tools.translation import Unit, verify_units

    fake = FakeBodhan(append_text=" 999")
    r, err = await call(make_ctx(fake), "translate_text", text="Plant trees near the school.", target_language="hi")
    assert not err and r["verification"]["status"] != "pass" and any("999" in w for w in r["verification"]["warnings"])
    src = "| Tree | Count |\n| --- | --- |\n| Mango | 12 |\n| Neem | 25 |"
    swapped = "| Tree | Count |\n| --- | --- |\n| Mango | 25 |\n| Neem | 12 |"
    v = await verify_units(make_ctx(fake), [Unit("table", src, swapped)], src, swapped, "en", "native", "en", back_translate=False)
    assert v["status"] == "fail" and any("table rows" in p for p in v["problems"])


# ---- N07 / N08 / N09 / N10: cache and in-flight lifecycle ------------------------------------
async def test_N07_warm_cache_works_under_a_tiny_cap(tmp_path):
    cache_dir = tmp_path / "cache"
    fake = FakeBodhan()
    first = ctx_with_cache(tmp_path, fake, cache_dir)
    r, err = await call(first, "synthesize_speech", text="নমস্কার, কেমন আছেন?", language="bn", format="wav")
    assert not err
    calls = dict(fake.calls)
    second = ctx_with_cache(tmp_path, fake, cache_dir, max_inr=0.0001)
    r, err = await call(second, "synthesize_speech", text="নমস্কার, কেমন আছেন?", language="bn", format="wav")
    assert not err and dict(fake.calls) == calls and r["cost_inr"] == 0


async def test_N08_cancelling_one_waiter_does_not_cancel_the_other(make_ctx):
    fake = FakeBodhan(delay=0.2)
    ctx = make_ctx(fake)
    a = asyncio.create_task(ctx.client.translate("Shared request", "hi"))
    await asyncio.sleep(0.01)
    b = asyncio.create_task(ctx.client.translate("Shared request", "hi"))
    await asyncio.sleep(0.01)
    a.cancel()
    meta, _ = await b
    assert meta["translation"] and fake.calls["translate"] == 1


async def test_N09_truncated_results_are_not_cached_across_runs(tmp_path):
    cache_dir = tmp_path / "cache"
    img = make_image(tmp_path / "p.png")
    bad = FakeBodhan(ocr_truncate=True, ocr_pages=["cut"])
    r, err = await call(ctx_with_cache(tmp_path, bad, cache_dir), "ocr_document", path=str(img))
    assert not err and r["incomplete_pages"] == [1]
    good = FakeBodhan(ocr_pages=["complete page text"])
    r, err = await call(ctx_with_cache(tmp_path, good, cache_dir), "ocr_document", path=str(img))
    assert not err and good.calls["ocr"] == 1 and r["incomplete_pages"] == []


async def test_N10_corrupt_cache_entries_are_refetched(tmp_path):
    cache_dir = tmp_path / "cache"
    fake = FakeBodhan()
    r, err = await call(ctx_with_cache(tmp_path, fake, cache_dir), "synthesize_speech", text="नमस्ते दुनिया।", language="hi", format="wav", verify=False)
    assert not err
    for f in cache_dir.glob("*.bin"):
        f.unlink()
    for f in cache_dir.glob("*.json"):
        f.write_bytes(b"\xff\xfe broken")
    before = fake.calls["speech"]
    r, err = await call(ctx_with_cache(tmp_path, fake, cache_dir), "synthesize_speech", text="नमस्ते दुनिया।", language="hi", format="wav", verify=False)
    assert not err and fake.calls["speech"] == before + 1


# ---- F02 / F03: output folder and setup boundaries --------------------------------------------
async def test_F02_output_inside_input_folder_is_not_an_input(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "source.txt").write_text("Trees give shade.", encoding="utf-8")
    chat = ScriptedChat([[("finish", {"status": "failed", "final_answer": "Stopping."})]])
    a = args(tmp_path, "List inputs", [src])
    a.out = str(src / "out")
    await run(a, transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    rep = json.loads((src / "out" / "report.json").read_text(encoding="utf-8"))
    assert [p.replace("\\", "/").split("/")[-1] for p in rep["inputs"]["files"]] == ["source.txt"]
    a2 = args(tmp_path, "Use an output file", [src / "out" / "report.json"])
    a2.out = str(src / "out")
    with pytest.raises(UsageError):
        await run(a2, transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    assert (src / "out" / "report.json").exists()  # nothing was moved


async def test_F03_unusable_cache_folder_still_gives_a_report(tmp_path):
    bad = tmp_path / "cachefile"
    bad.write_text("not a folder", encoding="utf-8")
    txt = tmp_path / "p.txt"
    txt.write_text("x", encoding="utf-8")
    chat = ScriptedChat([[("finish", {"status": "failed", "final_answer": "Stopping."})]])
    a = args(tmp_path, "Do it", [txt], cache_dir=str(bad), no_cache=False)
    await run(a, transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] == "failed" and any("not usable" in str(e.get("notes")) for e in trace if e["type"] == "run_start")


# ---- E05: sparse cues are reported, not silently longer than promised --------------------------
def test_E05_sparse_cues_keep_their_text():
    from bodhan_mcp.tools.audio_tools import split_cues

    cues = split_cues([{"start": 0, "end": 28, "text": "hello"}], 7)
    assert cues == [{"start": 0, "end": 28, "text": "hello"}]


# ---- resume: an interrupted run continues without re-billing finished work --------------------
class Interrupted(BaseException):
    pass


async def test_resume_continues_without_rebilling(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("The library opens at eight o'clock every morning.", encoding="utf-8")
    fake = FakeBodhan()
    plan = [("record_plan", {"steps": ["translate", "speak"], "deliverables": [{"kind": "audio", "language": "hi"}]})]

    def crash(m):
        raise Interrupted()

    first = ScriptedChat([plan, [("translate_text", {"path": str(txt), "target_language": "hi"})], crash])
    with pytest.raises(Interrupted):
        await run(args(tmp_path, "Read this aloud in Hindi", [txt]), transport=fake.transport, llm=PlannerLLM(client=first, model="s"))
    translate_calls = fake.calls["translate"]
    assert translate_calls > 0 and not (tmp_path / "out" / "report.json").exists()

    second = ScriptedChat([
        lambda m: [("synthesize_speech", {"path": last_ok(m, "path"), "language": "hi", "format": "wav"})],
        lambda m: [("finish", {"status": "completed", "final_answer": "Done.", "deliverables": [last_ok(m, "path")]})],
    ])
    a = args(tmp_path, None, [])
    a.resume = True
    await run(a, transport=fake.transport, llm=PlannerLLM(client=second, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] in ("completed", "completed_with_warnings")
    assert fake.calls["translate"] == translate_calls  # the translation done before the interruption was not repeated
    assert any(e["type"] == "run_resumed" for e in trace) and rep["steps"]["used"] == 4
    a.resume = True
    from bodhan_agent.state import ResumeError

    with pytest.raises(ResumeError):  # a finished run cannot be resumed
        await run(a, transport=fake.transport, llm=PlannerLLM(client=second, model="s"))


def test_wav_tone_helper_still_works():
    assert len(wav_tone(0.1)) > 44 and io.BytesIO(b"x")


# ---- parallel independent calls ----------------------------------------------------------------
async def test_independent_calls_in_one_turn_run_in_parallel_with_separate_costs(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("The first notice is about the library.", encoding="utf-8")
    b.write_text("The second notice is about the bus.", encoding="utf-8")
    fake = FakeBodhan(delay=0.4)
    chat = ScriptedChat([
        [("record_plan", {"steps": ["translate both"], "deliverables": [{"kind": "translation", "language": "hi"}]})],
        [("translate_text", {"path": str(a), "target_language": "hi", "verify": False}),
         ("translate_text", {"path": str(b), "target_language": "hi", "verify": False})],
        [("finish", {"status": "partial", "final_answer": "Translated both; not verified."})],
    ])
    await run(args(tmp_path, "Translate both notices to Hindi", [a, b]), transport=fake.transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    calls = [e for e in trace if e["type"] == "tool_call"]
    assert [c["step"] for c in calls] == [2, 3] or sorted(c["step"] for c in calls) == [2, 3]
    assert all(c["cost_inr"] > 0 for c in calls)
    # each request takes ~0.4 s: run in parallel they finish together; one after the other they would finish ~0.4 s apart
    assert abs(calls[0]["t"] - calls[1]["t"]) < 0.25
    assert len([e for e in trace if e["type"] == "api_call"]) == 2


def test_N04_raster_share_on_real_pdfs_without_stubs(tmp_path):
    from conftest import make_pdf

    from bodhan_mcp.documents import pdf_raster_share, pdf_text_layer

    scanned = make_pdf(tmp_path / "scan.pdf", 1)  # Pillow writes each page as one full-page image
    assert pdf_raster_share(scanned, 0) > 0.9
    text = "This digital page has a complete text layer and no pictures at all."
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf = b"%PDF-1.4\n" + b"".join(f"{i} 0 obj\n".encode() + o + b"\nendobj\n" for i, o in enumerate(objs, 1)) + b"trailer << /Root 1 0 R >>\n%%EOF\n"
    digital = tmp_path / "digital.pdf"
    digital.write_bytes(pdf)
    assert pdf_raster_share(digital, 0) == 0.0 and "complete text layer" in pdf_text_layer(digital, 0)


def test_N09_legacy_cache_entries_are_reused_only_when_complete(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    (d / "ok.json").write_text(json.dumps({"translation": "x", "truncated": False}), encoding="utf-8")
    (d / "cut.json").write_text(json.dumps({"translation": "x", "truncated": True}), encoding="utf-8")
    (d / "ocrcut.json").write_text(json.dumps({"markdown": "x", "finish_reason": "length"}), encoding="utf-8")
    (d / "audio.json").write_text(json.dumps({"format": "wav", "chars": 3}), encoding="utf-8")  # its .bin is missing
    cache = ResultCache(d)
    assert cache.get("ok") is not None
    assert cache.get("cut") is None and cache.get("ocrcut") is None and cache.get("audio") is None
