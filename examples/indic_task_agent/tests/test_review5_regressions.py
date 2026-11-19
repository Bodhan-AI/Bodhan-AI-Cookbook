"""Regression tests for review round 5 (commit 3f41986)."""

import json

import pytest
from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, last_ok, read_outputs

from bodhan_agent.cli import run
from bodhan_agent.llm import PlannerLLM
from bodhan_mcp.languages import detect_language
from bodhan_mcp.registry import invoke
from bodhan_mcp.tools import TOOL_BY_NAME


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


# 1. French made of words English shares ("a", "on", "et") is not English; real English still is
@pytest.mark.parametrize("text", ["On a un chat et on a un chien.", "Il a un chat.", "Elle est à la maison avec on a."])
def test_french_with_shared_short_words_is_not_english(text):
    assert detect_language(text).language != "en"


@pytest.mark.parametrize("text", ["The meeting is on Friday.", "Fill in the blanks.", "The summary covers the entire page.", "Not at ten as we said before",
                                  "Please bring two notebooks to class and a pen."])
def test_short_english_is_still_english(text):
    assert detect_language(text).language == "en"


async def test_french_is_never_copied_as_english(make_ctx):
    fake = FakeBodhan()
    r, err = await invoke(TOOL_BY_NAME["translate_text"], make_ctx(fake), {"text": "On a un chat et on a un chien.", "target_language": "en"})
    # too short to refuse for certain, so it is sent to the model (refused only on strong evidence) - never copied as English
    assert (err and r["error"]["code"] == "unsupported_language") or (not err and fake.calls["translate"] >= 1)


# 2. a summary written from a translation keeps the translation's source, so a source-bound contract is met
async def test_summary_with_source_paths_satisfies_a_source_bound_contract(tmp_path):
    a = tmp_path / "notice.txt"
    a.write_text("The library will be closed on Monday for repairs.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["summarise in Hindi"], "deliverables": [{"kind": "translation", "language": "hi", "source": "notice.txt"}]})
    summary = lambda m: [("write_text", {"name": "summary_en.md", "content": "The library is closed on Monday.",
                                         "source_paths": [str(a)]})]
    chat = ScriptedChat([[plan], [("read_text", {"path": str(a)})], summary, lambda m: [("translate_text", {"path": last_ok(m, "path"), "target_language": "hi"})],
                         lambda m: [("finish", {"status": "completed", "final_answer": "Summary translated.", "deliverables": [last_ok(m, "path")]})]])
    await run(args(tmp_path, "Summarise the notice in Hindi", [a]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert not [e for e in trace if e["type"] == "finish_rejected"]
    assert rep["status"] in ("completed", "completed_with_warnings")


async def test_source_paths_must_exist(make_ctx):
    r, err = await invoke(TOOL_BY_NAME["write_text"], make_ctx(FakeBodhan()), {"name": "s.md", "content": "x", "source_paths": ["nope.txt"]})
    assert err and r["error"]["code"] == "file_not_found"


# 3. priced planner retries are budgeted and failed attempts are charged
class _Flaky:
    """Fails the first `fail` planner requests with a 503 after 'sending' them, then behaves like `inner`."""

    class _E(Exception):
        status_code = 503

    def __init__(self, inner, fail=1):
        self.inner, self.fail, self.sent = inner, fail, 0
        self.chat = self
        self.completions = self

    async def create(self, **kw):
        self.sent += 1
        if self.sent <= self.fail:
            raise self._E("upstream unavailable")
        return await self.inner.create(**kw)


def _priced(tmp_path, txt, budget):
    return args(tmp_path, "Detect the language", [txt], max_cost_inr=budget, llm_input_inr_per_1m=1000.0, llm_output_inr_per_1m=1000.0)


async def test_failed_planner_attempt_before_a_success_is_charged(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees grow slowly.", encoding="utf-8")
    chat = ScriptedChat([[("finish", {"status": "unsupported", "final_answer": "Stopping."})]])
    await run(_priced(tmp_path, txt, 80.0), transport=FakeBodhan().transport, llm=PlannerLLM(client=_Flaky(chat), model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    charged = [e for e in trace if e["type"] == "llm_uncertain"]
    reply = [e for e in trace if e["type"] == "llm_call"]
    assert len(charged) == 1 and charged[0]["attempts"] == 1 and charged[0]["cost_inr"] > 0
    assert rep["cost"]["llm_inr"] == pytest.approx(charged[0]["cost_inr"] + reply[0]["cost_inr"], abs=1e-4)


async def test_planner_retry_is_not_sent_beyond_the_budget(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees grow slowly.", encoding="utf-8")
    flaky = _Flaky(ScriptedChat([]), fail=99)
    # one bounded planner call (about ₹27 at ₹1000 per 1M tokens) fits in ₹40, two do not
    await run(_priced(tmp_path, txt, 40.0), transport=FakeBodhan().transport, llm=PlannerLLM(client=flaky, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert flaky.sent == 1 and rep["stop_reason"] == "llm_error"
    assert rep["cost"]["total_counted_inr"] <= 40.0 + 1e-9


# ---- review round 6 --------------------------------------------------------------------------
async def test_source_paths_need_the_whole_current_file_to_have_been_read(make_ctx, tmp_path):
    notice = tmp_path / "notice.txt"
    notice.write_text("The library will be closed on Monday. " * 20, encoding="utf-8")
    ctx = make_ctx(FakeBodhan())
    w = TOOL_BY_NAME["write_text"]
    args_ = {"name": "s.md", "content": "A spaceship lands on Mars.", "source_paths": [str(notice)]}
    r, err = await invoke(w, ctx, args_)  # never read: naming a file is not evidence
    assert err and r["error"]["code"] == "invalid_argument"
    page, _ = await invoke(TOOL_BY_NAME["read_text"], ctx, {"path": str(notice), "max_chars": 200})
    r, err = await invoke(w, ctx, args_)  # first page only
    assert err and page["next_offset"] == 200
    while page["next_offset"] is not None:
        page, _ = await invoke(TOOL_BY_NAME["read_text"], ctx, {"path": str(notice), "offset": page["next_offset"], "max_chars": 200})
    r, err = await invoke(w, ctx, args_)
    assert not err and r["source_paths"] == [str(notice.resolve())]
    notice.write_text("Changed after it was read.", encoding="utf-8")  # the content read is no longer the file
    r, err = await invoke(w, ctx, {**args_, "name": "s2.md"})
    assert err


async def test_an_ascii_hint_does_not_turn_unidentified_speech_into_verified_english(make_ctx, tmp_path):
    from conftest import speechlike, write_wav

    fake = FakeBodhan(asr_override="Bonjour monsieur merci beaucoup")
    audio = write_wav(tmp_path / "fr.wav", speechlike(6, 16000))
    ctx = make_ctx(fake)
    r, err = await invoke(TOOL_BY_NAME["create_subtitles"], ctx, {"path": str(audio), "subtitle_language": "en", "source_language": "en"})
    assert err or r["verification"]["status"] != "pass"


async def test_a_deadline_during_retry_backoff_charges_only_the_sent_attempt(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees grow slowly.", encoding="utf-8")
    flaky = _Flaky(ScriptedChat([]), fail=99)
    await run(args(tmp_path, "Detect the language", [txt], max_cost_inr=40.0, max_seconds=1.0, llm_input_inr_per_1m=1000.0,
                   llm_output_inr_per_1m=1000.0), transport=FakeBodhan().transport, llm=PlannerLLM(client=flaky, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert flaky.sent == 1 and rep["stop_reason"] == "time_limit"  # the 2 s backoff outlives the 1 s deadline
    charged = [e for e in trace if e["type"] == "llm_uncertain"]
    assert len(charged) == 1 and charged[0]["attempts"] == 1


# ---- planner budget: a bound, not an average -------------------------------------------------
def test_prompt_bound_covers_byte_level_tokenizers_on_indic_text():
    from bodhan_agent.loop import prompt_token_bound

    msgs = [{"role": "tool", "content": "நீர் சுழற்சி பாடம் " * 200}]
    worst = len(msgs[0]["content"].encode("utf-8"))  # a byte-level tokenizer can need one token per byte
    assert prompt_token_bound(msgs, []) >= worst > 2 * len(msgs[0]["content"])


async def test_a_reply_costing_more_than_its_bound_is_reported(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees grow slowly.", encoding="utf-8")

    class Overcounting:  # a provider that reports far more prompt tokens than the request could contain
        def __init__(self):
            self.inner = ScriptedChat([[("finish", {"status": "unsupported", "final_answer": "Stopping."})]])
            self.chat = self.completions = self

        async def create(self, **kw):
            resp = await self.inner.create(**kw)
            resp.usage.prompt_tokens = 10_000_000
            return resp

    await run(_priced(tmp_path, txt, 80.0), transport=FakeBodhan().transport, llm=PlannerLLM(client=Overcounting(), model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["cost"]["llm_over_pre_call_bound_inr"] > 0 and any(e["type"] == "llm_estimate_exceeded" for e in trace)


# ---- review round 7 --------------------------------------------------------------------------
async def test_an_embed_hidden_in_a_comment_is_refused(make_ctx, tmp_path):
    src = tmp_path / "t.md"
    src.write_text("The library is closed on Monday.", encoding="utf-8")
    r, err = await invoke(TOOL_BY_NAME["write_text"], make_ctx(FakeBodhan()), {"name": "s.md", "content": f"A spaceship lands.\n\n<!--{{{{artifact:{src}}}}}-->"})
    assert err and r["error"]["code"] == "invalid_argument"


async def test_a_read_page_counts_only_if_it_reached_the_planner_whole(tmp_path):
    """A long page that would be clipped in the planner's context is shortened instead, so 'read in full' means seen."""
    doc = tmp_path / "long.txt"
    doc.write_text("".join(f"Line {i}: the library opens at eight and closes at six.\n" for i in range(200)), encoding="utf-8")
    plan = ("record_plan", {"steps": ["summarise"], "deliverables": [{"kind": "text", "language": "en", "source": "long.txt"}]})
    summary = ("write_text", {"name": "summary.md", "content": "The library opens at eight.", "source_paths": [str(doc)]})
    chat = ScriptedChat([[plan], [("read_text", {"path": str(doc), "max_chars": 20000})], [summary],
                         lambda m: [("finish", {"status": "completed", "final_answer": "Done.", "deliverables": [last_ok(m, "path")]})]])
    await run(args(tmp_path, "Summarise this file", [doc]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    reads = [json.loads(m["content"]) for m in chat.seen[2] if m.get("role") == "tool"]
    page = reads[-1]
    assert page["ok"] and page["next_offset"] is not None and len(page["text"]) < len(doc.read_text(encoding="utf-8"))  # shortened, arrived whole
    assert rep["status"] not in ("completed", "completed_with_warnings")  # the rest was never read, so the summary has no source


# ---- cost basis: metered vs bounded estimate -------------------------------------------------
async def test_a_translation_without_usage_is_labelled_an_estimate(make_ctx):
    import httpx

    fake = FakeBodhan()
    inner = fake.handle

    def no_usage(request):
        resp = inner(request)
        body = json.loads(resp.content)
        body.pop("usage", None)
        return httpx.Response(resp.status_code, json=body)

    fake.handle = no_usage
    events: list = []
    ctx = make_ctx(fake, events=events)
    meta, _ = await ctx.client.translate("The school opens at nine.", "hi")
    call = [e for e in events if e.get("type") == "api_call" and e.get("cache") == "miss"][-1]
    assert call["cost_basis"] == "bounded_estimate" and ctx.ledger.summary()["bodhan_billed_estimated_inr"] > 0


# ---- CodeRabbit review (stable diff) ---------------------------------------------------------
def test_a_general_deliverable_does_not_take_the_only_artifact_a_specific_one_needs(tmp_path):
    from bodhan_agent.loop import Agent, Settings
    from bodhan_agent.trace import TraceWriter
    from bodhan_mcp.runtime import Artifact, CostLedger, ResultCache, RunContext

    ctx = RunContext(artifacts_dir=tmp_path / "art", ledger=CostLedger(), cache=ResultCache(None))
    ctx.artifacts_dir.mkdir()
    arts = []
    for name, origin in (("a_hi.md", ["a.txt"]), ("b_hi.md", ["b.txt"])):
        f = ctx.artifacts_dir / name
        f.write_text("x", encoding="utf-8")
        art = Artifact(f, "translation", "translate_text", "hi", "indic-translate", {"status": "pass"}, origin=origin)
        ctx.register(art)
        arts.append(art)
    agent = Agent("t", ctx, TraceWriter(tmp_path / "t.jsonl"), Settings(), None, [], [], out=None)
    agent.plan = {"deliverables": [{"kind": "translation", "language": "hi"}, {"kind": "translation", "language": "hi", "source": "a.txt"}]}
    assert agent._contract_gaps(arts, "Done.") == []  # greedy order would give a_hi.md to the general one and fail the specific one


def test_a_resumed_trace_never_glues_an_event_onto_a_half_written_line(tmp_path):
    from bodhan_agent.trace import TraceWriter

    t = tmp_path / "trace.jsonl"
    t.write_text('{"type": "run_start"}\n{"type": "api_ca', encoding="utf-8")  # killed mid-write
    TraceWriter(t, append=True).write({"type": "run_resumed"})
    lines = t.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["type"] == "run_resumed"


def test_transparent_images_are_flattened_onto_white(tmp_path):
    from PIL import Image

    from bodhan_mcp.documents import _encode_image

    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))  # fully transparent: as RGB it would be black
    data, _ = _encode_image(img, prefer_png=True)
    import io

    assert Image.open(io.BytesIO(data)).convert("RGB").getpixel((5, 5)) == (255, 255, 255)


def test_a_priced_planner_call_cut_off_by_a_kill_is_charged_on_resume(tmp_path):
    from bodhan_agent.state import ledger_from_trace

    t = tmp_path / "trace.jsonl"
    events = [{"type": "run_start"}, {"type": "llm_dispatch", "estimate_inr": 0.5, "max_attempts": 3},  # killed here
              {"type": "run_resumed"}, {"type": "llm_dispatch", "estimate_inr": 0.4, "max_attempts": 3},
              {"type": "llm_call", "prompt_tokens": 10, "completion_tokens": 2, "cost_inr": 0.1}]
    t.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    st = ledger_from_trace(t)
    assert st["llm_cost"] == pytest.approx(0.5 * 3 + 0.1)  # the unsettled call at every attempt it could make, plus the settled one


# ---- competitive review, tier 0 --------------------------------------------------------------
def test_planner_retries_follow_the_providers_wait_hint():
    from bodhan_agent.llm import MAX_RETRY_WAIT_S, retry_wait

    class RateLimited(Exception):
        status_code = 429

    assert retry_wait(RateLimited("Rate limit reached. Please try again in 7.5s."), 1) == 8.0
    assert retry_wait(RateLimited("usage_limit_reached resets_in_seconds: 5007"), 1) == MAX_RETRY_WAIT_S
    assert retry_wait(RateLimited("busy"), 2) == 4.0


def test_the_mcp_server_never_crashes_on_an_unwritable_working_folder(tmp_path, monkeypatch):
    from bodhan_mcp.server import artifacts_folder

    locked = tmp_path / "locked"
    locked.write_text("not a folder", encoding="utf-8")  # mkdir under a file fails on every OS
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.cwd", lambda: locked)
    out = artifacts_folder(None)
    assert out.is_dir() and "locked" not in str(out)
