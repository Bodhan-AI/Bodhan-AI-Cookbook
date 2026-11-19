"""Regression tests for review round 4 (commit 37f5bd2)."""

import asyncio
import json
from pathlib import Path

import jsonschema
import pytest
from conftest import speechlike, write_wav
from fakes import FakeBodhan, ScriptedChat
from test_agent_e2e import args, last_ok, read_outputs

from bodhan_agent import state as checkpoint
from bodhan_agent.cli import run
from bodhan_agent.llm import LLMError, PlannerLLM
from bodhan_agent.loop import Agent, Settings
from bodhan_agent.trace import TraceWriter
from bodhan_mcp.registry import invoke
from bodhan_mcp.runtime import Artifact, sha256_bytes
from bodhan_mcp.tools import TOOL_BY_NAME
from bodhan_mcp.tools.schemas import output_schema
from bodhan_mcp.tools.translation import Unit, verify_units


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.setenv(k, "test-key")


async def call(ctx, tool_name, **a):
    return await invoke(TOOL_BY_NAME[tool_name], ctx, a)


# 1. resume counts spend settled after the last checkpoint, and unsettled dispatches as uncertain
def test_ledger_is_rebuilt_from_the_trace(tmp_path):
    trace = tmp_path / "trace.jsonl"
    events = [{"type": "run_start"},
              {"type": "api_dispatch", "model": "indic-translate", "reservation_id": 1, "amount_inr": 0.01},
              {"type": "api_call", "model": "indic-translate", "reservation_id": 1, "cache": "miss", "billing": "billed", "cost_inr": 0.004},
              {"type": "api_dispatch", "model": "indic-speak", "reservation_id": 2, "amount_inr": 0.05},  # process died here
              {"type": "api_call", "cache": "this_run"},
              {"type": "llm_call", "prompt_tokens": 100, "completion_tokens": 10, "cost_inr": None}]
    trace.write_text("\n".join(json.dumps(e) for e in events) + '\n{"type": "api_ca', encoding="utf-8")
    st = checkpoint.ledger_from_trace(trace)
    assert st["billed"] == pytest.approx(0.004) and st["uncertain"] == pytest.approx(0.05)
    assert st["calls"] == 1 and st["cache_hits"] == 1 and st["llm_prompt_tokens"] == 100


# 2. a source that changed after the checkpoint keeps its descendants unusable
def test_resume_keeps_failed_evidence_for_changed_sources(make_ctx, tmp_path):
    ctx = make_ctx(FakeBodhan())
    src = ctx.artifacts_dir / "ocr.md"
    src.write_text("page text", encoding="utf-8")
    wrapper = ctx.artifacts_dir / "wrap.md"
    wrapper.write_text("page text", encoding="utf-8")
    states = [Artifact(src, "ocr", "ocr_document").to_state(), Artifact(wrapper, "text", "write_text", depends_on=[str(src)]).to_state()]
    src.write_text("edited after the checkpoint", encoding="utf-8")
    trace = TraceWriter(tmp_path / "t.jsonl")
    agent = Agent("t", ctx, trace, Settings(), None, [], [], out=None)
    agent._restore({"steps": 0, "plan": None, "plan_version": 0, "finish_rejections": 0, "budget_errors": 0, "tool_log": [], "messages": [],
                    "ledger": {}, "artifacts": states})
    assert agent._effective_status(ctx.artifacts[str(wrapper)]) == "fail" and not agent._usable(ctx.artifacts[str(wrapper)])


# 3. one artifact per declared deliverable, sources respected, optional outputs allowed to be absent
async def test_contracts_are_one_to_one_source_bound_and_allow_optional(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("The first notice about the library.", encoding="utf-8")
    b.write_text("The second notice about the bus.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["translate both"], "deliverables": [
        {"kind": "translation", "language": "hi", "source": "a.txt"}, {"kind": "translation", "language": "hi", "source": "b.txt"},
        {"kind": "audio", "language": "hi", "optional": True}]})
    only_a = lambda m: [("finish", {"status": "completed", "final_answer": "Done.", "deliverables": [last_ok(m, "path")]})]
    chat = ScriptedChat([[plan], [("translate_text", {"path": str(a), "target_language": "hi"})], only_a,
                         [("translate_text", {"path": str(b), "target_language": "hi"})],
                         lambda m: [("finish", {"status": "completed", "final_answer": "Both translated.",
                                                "deliverables": [str(tmp_path / "out" / "artifacts" / "a_hi.md"), last_ok(m, "path")]})]])
    await run(args(tmp_path, "Translate both notices to Hindi", [a, b]), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    rejected = [e for e in trace if e["type"] == "finish_rejected"]
    assert rejected and "from b.txt" in str(rejected[0]["reasons"])
    assert rep["status"] in ("completed", "completed_with_warnings")


# 4. a passage whose language cannot be identified is never silently left unchecked
async def test_unchecked_passages_warn(make_ctx):
    units = [Unit("text", "ఇది తెలుగు వాక్యం మరియు ఇది పరీక్ష", "This is a Telugu sentence and a test"),
             Unit("text", "zorblax quintor velmish", "zorblax quintor velmish")]
    v = await verify_units(make_ctx(FakeBodhan()), units, "x", "x", "en", "native", None)
    assert v["status"] != "pass" and any("not back-translated" in w for w in v["warnings"])


# 5. the export's own staging folder is checked against the source
def test_export_staging_cannot_be_the_source(tmp_path):
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location("export_runs", Path(__file__).resolve().parents[1] / "scripts" / "export_runs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / f"export.staging-{os.getpid()}"
    (src / "r1").mkdir(parents=True)
    (src / "r1" / "report.json").write_text("{}", encoding="utf-8")
    with pytest.raises(mod.ExportError):
        mod.export(src, tmp_path / "export", tmp_path / "media")
    assert (src / "r1" / "report.json").exists()


# 6. when every waiter gives up, the request is cancelled and settled, not left running
async def test_cancelling_all_waiters_cancels_the_request(make_ctx):
    fake = FakeBodhan(delay=1.0)
    ctx = make_ctx(fake)
    a = asyncio.create_task(ctx.client.translate("Abandoned request", "hi"))
    b = asyncio.create_task(ctx.client.translate("Abandoned request", "hi"))
    await asyncio.sleep(0.05)
    a.cancel()
    b.cancel()
    await asyncio.gather(a, b, return_exceptions=True)
    await asyncio.sleep(0.05)
    assert not ctx.client._tasks and ctx.ledger.reserved == pytest.approx(0) and ctx.ledger.uncertain > 0


# 7. work can never use the step reserved for finish
async def test_last_step_is_reserved_for_finish(tmp_path):
    txt = tmp_path / "p.txt"
    txt.write_text("Trees.", encoding="utf-8")
    plan = ("record_plan", {"steps": ["x"], "deliverables": [{"kind": "answer"}]})
    detect = ("detect_language", {"path": str(txt)})
    chat = ScriptedChat([[plan, detect, detect, detect], [("finish", {"status": "partial", "final_answer": "Stopping."})]])
    await run(args(tmp_path, "Do it", [txt], max_steps=3), transport=FakeBodhan().transport, llm=PlannerLLM(client=chat, model="s"))
    out, rep, trace, md = read_outputs(tmp_path)
    assert len([s for s in rep["steps"]["tool_calls"] if s["tool"] == "detect_language"]) == 1
    assert rep["stop_reason"] == "finished" and rep["steps"]["used"] == 3


# 8. values swapped between columns of one row fail
async def test_column_swaps_within_a_row_fail(make_ctx):
    src = "| A | B |\n| --- | --- |\n| 12 | 25 |"
    out = "| A | B |\n| --- | --- |\n| 25 | 12 |"
    v = await verify_units(make_ctx(FakeBodhan()), [Unit("table", src, out)], src, out, "en", "native", "en", back_translate=False)
    assert v["status"] == "fail"


# 9. silent audio still matches the advertised output schema
async def test_silent_audio_result_matches_schema(make_ctx, tmp_path):
    import numpy as np

    silent = write_wav(tmp_path / "silence.wav", np.zeros(16000 * 3, dtype=np.float32))
    r, err = await call(make_ctx(FakeBodhan()), "transcribe_audio", path=str(silent))
    assert not err and r["segments"] == 0
    jsonschema.validate(r, output_schema("transcribe_audio"))


# 10. a well-formed cache entry with unusable content is refetched
async def test_malformed_cached_content_is_refetched(tmp_path):
    from bodhan_mcp.client import BodhanClient
    from bodhan_mcp.config import BodhanKeys
    from bodhan_mcp.runtime import CACHE_VERSION, CostLedger, ResultCache, cache_key

    d = tmp_path / "cache"
    d.mkdir()
    params = {"target_language": "hi", "script": "native", "max_output_tokens": 256}
    key = cache_key("indic-translate", sha256_bytes(b"Hi"), params)
    (d / f"{key}.json").write_text(json.dumps({"v": CACHE_VERSION, "key": key, "meta": {"oops": 1}, "blob_sha256": None}), encoding="utf-8")
    fake = FakeBodhan()
    client = BodhanClient(BodhanKeys("a", "b", "c", "d"), CostLedger(), ResultCache(d), transport=fake.transport)
    meta, _ = await client.translate("Hi", "hi")
    assert meta["translation"] and fake.calls["translate"] == 1


# 11. a short French sentence is not passed through as English
async def test_short_french_is_translated_not_copied(make_ctx):
    fake = FakeBodhan()
    r, err = await call(make_ctx(fake), "translate_text", text="Il a un chat.", target_language="en")
    assert not err and fake.calls["translate"] >= 1


# 12. a priced planner call is never sent without an output cap
async def test_priced_planner_refuses_an_unboundable_endpoint():
    class Rejecting:
        class _E(Exception):
            status_code = 400

        def __init__(self):
            self.chat = self
            self.completions = self
            self.sent = []

        async def create(self, **kw):
            self.sent.append(set(kw))
            cap = "max_tokens" if "max_tokens" in kw else "max_completion_tokens"
            raise self._E(f"unsupported parameter {cap}")

    client = Rejecting()
    llm = PlannerLLM(client=client, model="m")
    with pytest.raises(LLMError, match="cannot be bounded"):
        await llm.chat([{"role": "user", "content": "x"}], [{"type": "function", "function": {"name": "finish", "parameters": {}}}], max_tokens=100)
    assert all("max_tokens" in s or "max_completion_tokens" in s for s in client.sent)


def test_wav_helper_is_usable():
    assert speechlike(1, 16000).size == 16000
