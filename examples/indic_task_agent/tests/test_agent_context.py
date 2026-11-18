"""Context compaction (small LLM rate limits), 413 recovery and the repeated-failure guard."""

from __future__ import annotations

import json

from bodhan_agent.agent import compact_history
from bodhan_agent.llm import LLMReply
from tests.fakes.fake_llm import ScriptedLLM, result
from tests.test_agent_e2e import PLAN, _run


def _history(n: int) -> list[dict]:
    msgs = [{"role": "system", "content": "s" * 2000}, {"role": "user", "content": "task"}]
    for i in range(n):
        args = {"text": "x" * 3000, "why": "w"}
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "translate_text", "arguments": json.dumps(args)},
                    }
                ],
            }
        )
        data = {"output_path": f"/out/file{i}.md", "preview": "y" * 4000, "coverage_ok": True}
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": json.dumps(data)})
    return msgs


def test_compaction_keeps_paths_and_latest_turn():
    msgs = _history(6)
    before = len(json.dumps(msgs))
    compact_history(msgs, [], budget=3000)
    assert len(json.dumps(msgs)) < before / 2
    first_tool = json.loads(msgs[3]["content"])
    assert first_tool["older_result_summary"]["output_path"] == "/out/file0.md"
    assert "y" * 4000 in msgs[-1]["content"]  # latest result untouched
    assert all(
        set(m) <= {"role", "content", "tool_calls", "tool_call_id"} for m in msgs
    )  # no private keys sent to the API


class TooLargeOnce(ScriptedLLM):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.raised = False

    async def complete(self, messages, tools, force_tool=None) -> LLMReply:
        if force_tool is None and not self.raised:
            self.raised = True
            err = RuntimeError("Request too large for model on tokens per minute (TPM)")
            err.status_code = 413
            raise err
        return await super().complete(messages, tools, force_tool)


async def test_request_too_large_is_compacted_and_retried(fake, tmp_path):
    turns = [lambda s: [("finish", {"status": "impossible", "answer_en": "Nothing to do.", "artifacts": []})]]
    state, report, trace, out = await _run(fake, tmp_path, "noop", [], TooLargeOnce(PLAN, turns))
    assert report["status"] == "impossible"
    assert any(t["kind"] == "llm_retry" for t in trace)


async def test_repeated_failures_get_advice_then_block(fake, tmp_path):
    fake.state.fail_next["/v1/chat/completions"] = [400] * 20
    fake.state.fail_next["/translate"] = [400] * 20
    call = ("translate_text", {"target_language": "hi", "text": "Hello", "why": "try"})
    turns = [
        lambda s: [call],
        lambda s: [("translate_text", {**call[1], "text": "Hello there"})],
        lambda s: [("translate_text", {**call[1], "text": "Hi"})],
        lambda s: [("translate_text", {**call[1], "text": "Hey"})],
        lambda s: [("finish", {"status": "failed", "answer_en": "Translation is unavailable.", "artifacts": []})],
    ]
    llm = ScriptedLLM(PLAN, turns)
    state, report, trace, out = await _run(fake, tmp_path, "Translate Hello to Hindi", [], llm)
    assert "advice" in json.dumps(result(llm.seen, "translate_text")) or report["status"] == "failed"
    guards = [t for t in trace if t["kind"] == "guard" and t.get("error_code") == "repeated_failure"]
    assert len(guards) == 1
    assert report["status"] == "failed" and report["artifacts"] == []


async def test_text_reply_without_finish_gets_a_forced_finish(fake, tmp_path):
    turns = [
        lambda s: [],  # the model "says" it finished but calls no tool
        lambda s: [("finish", {"status": "impossible", "answer_en": "Translation is unavailable.", "artifacts": []})],
    ]
    llm = ScriptedLLM(PLAN, turns)
    state, report, trace, out = await _run(fake, tmp_path, "noop", [], llm)
    assert llm.tools_offered[-1] == ["finish"]
    assert report["status"] == "impossible" and report["stop_reason"] is None


def test_optional_arguments_accept_null():
    import asyncio as _a

    from bodhan_agent.prompts import FINISH_TOOL, PLAN_TOOL, llm_tool_schemas
    from bodhan_mcp.server import mcp

    tools = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in _a.run(mcp.list_tools())
    ]
    speak = next(t for t in llm_tool_schemas(tools) if t["function"]["name"] == "synthesize_speech")["function"][
        "parameters"
    ]
    assert speak["properties"]["voice"]["type"] == ["string", "null"]
    assert speak["properties"]["language"]["type"] == "string"  # required stays strict
    assert PLAN_TOOL["function"]["parameters"]["properties"]["input_languages"]["type"] == ["string", "null"]
    assert FINISH_TOOL["function"]["parameters"]["properties"]["assumptions"]["type"] == ["array", "null"]


class PlanBreaks(ScriptedLLM):
    async def complete(self, messages, tools, force_tool=None) -> LLMReply:
        if force_tool == "submit_plan":
            self.calls += 1
            err = RuntimeError("Tool call validation failed: parameters for tool submit_plan did not match schema")
            err.status_code = 400
            raise err
        return await super().complete(messages, tools, force_tool)


async def test_broken_plan_step_falls_back_to_step_by_step(fake, tmp_path):
    turns = [lambda s: [("finish", {"status": "impossible", "answer_en": "French is not supported.", "artifacts": []})]]
    state, report, trace, out = await _run(fake, tmp_path, "Translate this into French", [], PlanBreaks(PLAN, turns))
    assert report["status"] == "impossible" and "French" in report["answer_en"]
    assert any(t["kind"] == "plan_failed" for t in trace)


async def test_incomplete_result_may_be_retried_and_cache_fills_the_gaps(fake, tmp_path):
    from bodhan_agent.agent import Agent, Budgets
    from bodhan_agent.mcp_bridge import ToolBridge
    from bodhan_agent.report import write_outputs
    from tests.fakes.fake_bodhan import tone_wav

    clip = tmp_path / "note.wav"
    tone_wav(clip, 40, 220, gaps=[(19, 19.6)])
    fake.state.fail_next["/v1/audio/transcriptions"] = [429, 429, 429]  # one chunk exhausts its 2 attempts
    args = {"path": str(clip), "language": "hi", "why": "transcribe"}
    turns = [
        lambda s: [("transcribe_audio", args)],
        lambda s: [("transcribe_audio", args)],
        lambda s: [
            (
                "finish",
                {
                    "status": "success",
                    "answer_en": "Done.",
                    "artifacts": [
                        {"path": result(s, "transcribe_audio")["transcript_path"], "description": "transcript"}
                    ],
                },
            )
        ],
    ]

    def factory(env):
        return ToolBridge(
            env={
                **env,
                "BODHAN_BASE_URL": fake.url,
                "BODHAN_API_KEY": "k",
                "BODHAN_CACHE_DIR": str(tmp_path / "c"),
                "BODHAN_MAX_RETRIES": "1",
                "BODHAN_MAX_CONCURRENCY": "1",
            }
        )

    agent = Agent("Transcribe this", [str(clip)], tmp_path / "out", Budgets(), ScriptedLLM(PLAN, turns), factory)
    report = write_outputs(await agent.run(), "scripted")
    tools = [json.loads(x) for x in (tmp_path / "out" / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = [t for t in tools if t["kind"] == "tool" and t["tool"] == "transcribe_audio"]
    assert calls[0]["result_summary"]["complete"] is False and calls[1]["result_summary"]["complete"] is True
    assert report["status"] == "success"
