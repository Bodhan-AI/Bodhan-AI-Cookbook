"""Hidden-task cases found by offline probes (competitive review, tier 2)."""

import json
import unicodedata

import pytest
from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, read_outputs

from bodhan_agent.cli import run
from bodhan_agent.llm import PlannerLLM
from bodhan_mcp.documents import decode_text, text_layer_is_usable
from bodhan_mcp.errors import ToolError
from bodhan_mcp.languages import detect_language, normalize_language
from bodhan_mcp.registry import invoke
from bodhan_mcp.tools import TOOL_BY_NAME
from bodhan_mcp.tools.common import ProvenanceGuard


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


# 11. the provenance gate compares what a reader sees, knows input file names, and removes only unknown text
def test_known_text_matches_across_unicode_forms_and_invisible_joiners():
    g = ProvenanceGuard()
    g.add("ज़मीन पर पानी")  # precomposed nukta letter
    g.check(unicodedata.normalize("NFD", "ज़मीन पर पानी"), "answer")  # combining nukta
    g.check("ज़मीन\u200b पर पानी", "answer")  # with a zero-width space
    with pytest.raises(ToolError):
        g.check("नई बात", "answer")


async def test_an_answer_naming_indian_language_files_is_accepted(tmp_path):
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "క్లిప్_1.txt").write_text("Telugu notes.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["inspect", "answer"], "deliverables": [{"kind": "answer"}]})
    answer = ("finish", {"status": "completed", "final_answer": "The file క్లిప్_1.txt contains English notes."})
    chat = ScriptedChat([[plan], [("detect_language", {"path": str(folder / "క్లిప్_1.txt")})], [answer]])
    await run(args(tmp_path, "What language are these notes in?", [folder]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] == "completed" and "క్లిప్_1.txt" in rep["final_answer"]


def test_only_the_unknown_indian_language_text_is_removed():
    g = ProvenanceGuard()
    g.add("नमस्ते दुनिया")
    out = g.redact("The greeting is नमस्ते दुनिया and the planner added नई बात here.")
    assert "नमस्ते दुनिया" in out and "नई बात" not in out and "The greeting is" in out and "removed" in out


# 12. a Windows-1252 file is read as Latin text, not as UTF-16 "Chinese"
def test_windows_1252_text_is_not_decoded_as_utf16():
    raw = "Café menu: crème brûlée, 5 €".encode("cp1252")
    assert decode_text(raw) == "Café menu: crème brûlée, 5 €"
    assert decode_text("नमस्ते".encode("utf-16")) == "नमस्ते"  # real UTF-16 (with BOM) still works


# 13. a legacy-font (Kruti Dev) text layer is not trusted: the page is OCR'd
def test_a_legacy_font_text_layer_is_not_trusted():
    kruti = "dsUnzh; fo|ky; ds Nk=ksa us vkt ,d ubZ ;kstuk dh 'kq:vkr dh vkSj lHkh f'k{kdksa us bldk Lokxr fd;kA"
    assert not text_layer_is_usable(kruti)
    assert text_layer_is_usable("The central school started a new plan today and all the teachers welcomed it warmly.")


# 14. a French SOURCE is refused before anything is paid for
async def test_a_french_source_is_refused(make_ctx):
    fake = FakeBodhan()
    r, err = await invoke(TOOL_BY_NAME["translate_text"], make_ctx(fake),
                          {"text": "La bibliothèque sera fermée le lundi pour les travaux de rénovation.", "target_language": "hi"})
    assert err and r["error"]["code"] == "unsupported_language" and "French" in r["error"]["message"] and fake.calls["translate"] == 0


def test_romanized_hindi_is_not_mistaken_for_a_european_language():
    assert detect_language("Kal school mein exam hai aur main late ho gaya tha.").language is None


# 15. romanized deliverables, three-letter codes and "all languages" plans
def test_three_letter_codes_and_romanized_deliverables():
    assert normalize_language("hin") == "hi" and normalize_language("TAM") == "ta"


async def test_a_plan_can_declare_romanized_and_many_deliverables(tmp_path):
    txt = tmp_path / "n.txt"
    txt.write_text("The library opens at eight.", encoding="utf-8")
    many = [{"kind": "translation", "language": c} for c in ("hi", "ta", "te", "bn", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur", "ne", "sa")]
    plan = ("record_plan", {"steps": ["translate"], "deliverables": [{"kind": "translation", "language": "hi-Latn"}, *many]})
    chat = ScriptedChat([[plan], [("finish", {"status": "partial", "final_answer": "Stopping."})]])
    await run(args(tmp_path, "Translate this into every language you support, plus romanized Hindi", [txt]), transport=FakeBodhan().transport,
              llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    declared = rep["plan"]["deliverables"]  # the trace shortens long records; the report keeps the whole plan
    assert declared[0]["language"] == "hi" and "romanized" in declared[0]["description"]
    assert len(declared) == 15 and not [e for e in trace if e["type"] == "control_rejected"] and json.dumps(rep)


# Provider error messages can carry account ids; key formats of other providers count as secrets
def test_export_scrubs_account_ids_and_other_providers_keys(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("export_runs", Path(__file__).resolve().parents[1] / "scripts" / "export_runs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.scrub("Request too large for model in organization `org_01exampleexample0000000` service tier", tmp_path, [])
    assert "org_01example" not in text and "<account-id>" in text
    assert mod.leaks("key gsk_" + "a" * 40) and mod.leaks("key AIza" + "b" * 35)


# Some endpoints cap a whole request at ~8k tokens: compaction keeps shortening until it fits
async def test_compaction_keeps_shrinking_until_the_request_fits(tmp_path):
    from fakes import _Obj  # noqa: F401  (fakes provides the scripted planner)

    txt = tmp_path / "p.txt"
    txt.write_text("The library opens at eight. " * 200, encoding="utf-8")
    plan = ("record_plan", {"steps": ["read"], "deliverables": [{"kind": "answer"}]})
    inner = ScriptedChat([[plan], [("read_text", {"path": str(txt)})], [("read_text", {"path": str(txt), "offset": 3000})],
                          [("finish", {"status": "completed", "final_answer": "It opens at eight."})]])

    class Err(Exception):
        status_code = 413

    calls = {"n": 0}

    class Capped:
        def __init__(self):
            self.chat = self.completions = self

        async def create(self, **kw):
            calls["n"] += 1
            if len(json.dumps(kw["messages"])) > 9000:  # "Request too large ... Limit 8000"
                raise Err("Error code: 413 - Request too large for model on tokens per minute (TPM): Limit 8000, Requested 9029")
            return await inner.create(**kw)

    await run(args(tmp_path, "When does the library open?", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=Capped(), model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] in ("completed", "completed_with_warnings"), rep["final_answer"]
    assert any(e["type"] == "context_compacted" for e in trace)


# 23. generated speech is delivered together with the text it reads (final4 d1 listed only the MP3)
async def test_audio_is_delivered_with_its_script(tmp_path):
    from test_agent_e2e import last_ok

    txt = tmp_path / "summary_en.txt"
    txt.write_text("The water cycle moves water between the sea, the sky and the land.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["translate", "speak"], "deliverables": [{"kind": "audio", "language": "ta"}]})
    chat = ScriptedChat([[plan], [("translate_text", {"path": str(txt), "target_language": "ta", "output_name": "summary_ta"})],
                         lambda m: [("synthesize_speech", {"path": last_ok(m, "path"), "language": "ta", "format": "wav"})],
                         lambda m: [("finish", {"status": "completed", "final_answer": "Made the Tamil audio.", "deliverables": [last_ok(m, "path")]})]])
    await run(args(tmp_path, "Read this summary aloud in Tamil", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    kinds = sorted(d["kind"] for d in rep["deliverables"])
    assert kinds == ["audio", "translation"] and any(e["type"] == "deliverable_added" for e in trace)


# 18. a bare file name is found in the input folders
async def test_a_bare_file_name_resolves_inside_the_input_folder(make_ctx, tmp_path):
    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "note.txt").write_text("The library opens at eight.", encoding="utf-8")
    ctx = make_ctx(FakeBodhan(), read_roots=[folder])
    r, err = await invoke(TOOL_BY_NAME["read_text"], ctx, {"path": "note.txt"})
    assert not err and "eight" in r["text"]


# 19. a planner outage still names the missing inputs, which code checked before planning
async def test_a_planner_outage_still_reports_missing_inputs(tmp_path):
    class Down:
        def __init__(self):
            self.chat = self.completions = self

        async def create(self, **kw):
            raise ConnectionError("planner unreachable")

    await run(args(tmp_path, "Translate the letter", [tmp_path / "letter.pdf"]), transport=FakeBodhan().transport,
              llm=PlannerLLM(client=Down(), model="s", max_attempts=1))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] == "failed" and "letter.pdf" in rep["final_answer"]


# 20. verify_speech names its real field in errors
async def test_verify_speech_error_names_the_real_field(make_ctx, tmp_path):
    from conftest import speechlike, write_wav

    wav = write_wav(tmp_path / "a.wav", speechlike(2, 16000))
    r, err = await invoke(TOOL_BY_NAME["verify_speech"], make_ctx(FakeBodhan()), {"audio_path": str(wav), "language": "hi"})
    assert err and "expected_path" in r["error"]["message"]


# CodeRabbit (tier 0-2 diff) ------------------------------------------------------------------
def test_redaction_ignores_how_the_words_are_spaced():
    g = ProvenanceGuard()
    out = g.redact("Answer: नई\nबात  यहाँ, done.")
    assert "नई" not in out and "बात" not in out and "Answer:" in out and "done." in out


def test_an_interrupt_mid_turn_keeps_the_turn_replayable(tmp_path):
    """Ctrl-C while a tool call is in flight (in a child process: pytest treats KeyboardInterrupt as its own)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    from bodhan_agent import state as checkpoint

    (tmp_path / "p.txt").write_text("The library opens at eight o'clock every morning.", encoding="utf-8")
    child = [sys.executable, str(Path(__file__).with_name("_kill_resume_child.py"))]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **{k: "test-key" for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY",
                                                                                 "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY")}}
    first = subprocess.run([*child, "interrupt", str(tmp_path)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert "KeyboardInterrupt" in first.stderr
    saved = checkpoint.load(tmp_path / "out")
    assert saved["pending"] and saved["pending"]["calls"][0]["name"] == "translate_text"  # the turn to replay is still there
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["provisional"] and report["stop_reason"] == "interrupted"
    done = subprocess.run([*child, "resume", str(tmp_path)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] in ("completed", "completed_with_warnings") and not rep.get("provisional")


def test_finish_accepts_as_many_deliverables_as_a_plan_may_declare():
    from bodhan_agent.loop import FinishIn

    assert FinishIn(status="completed", final_answer="Done.", deliverables=[f"a{i}.md" for i in range(40)])


async def test_an_interrupt_during_teardown_after_finish_keeps_the_final_report(tmp_path, monkeypatch):
    import asyncio

    from bodhan_mcp.client import BodhanClient

    txt = tmp_path / "p.txt"
    txt.write_text("The library opens at eight.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["read", "answer"], "deliverables": [{"kind": "answer"}]})
    chat = ScriptedChat([[plan], [("read_text", {"path": str(txt)})], [("finish", {"status": "completed", "final_answer": "At eight."})]])

    async def interrupted_close(self):
        raise asyncio.CancelledError()

    monkeypatch.setattr(BodhanClient, "aclose", interrupted_close)
    with pytest.raises(asyncio.CancelledError):
        await run(args(tmp_path, "When does the library open?", [txt]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    rep = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert rep["status"] == "completed" and not rep.get("provisional")
