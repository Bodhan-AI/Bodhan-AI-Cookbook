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
