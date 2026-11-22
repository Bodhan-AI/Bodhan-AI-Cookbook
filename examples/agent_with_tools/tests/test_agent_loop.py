"""End-to-end agent loop with a scripted fake LLM and fake tools: verification, budgets, timeouts, trace."""

import asyncio
import json

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from bodhan_agent.graph import build_graph, initial_state
from bodhan_agent.trace import RunRecorder
from bodhan_agent.verify import check_translation, compare_spoken

HINDI = "नमस्ते दुनिया। आप कैसे हैं।"


class ScriptedLLM:
    """Replays AIMessages in order; records what it was shown."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        self.seen.append(messages)
        return self.replies.pop(0)


_ids = iter(range(10_000))


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"id-{next(_ids)}", "type": "tool_call"}])


def make_tools(transcript=HINDI, delay=0.0):
    log = []

    async def speak_text(text: str, output_path: str, language: str | None = None) -> str:
        log.append("speak_text")
        await asyncio.sleep(delay)
        return json.dumps({"ok": True, "audio_path": output_path, "language": language, "duration_s": 6.0, "cost_inr": len(text) * 6 / 10_000})

    async def transcribe_audio(audio_path: str, language: str | None = None) -> str:
        log.append("transcribe_audio")
        return json.dumps({"ok": True, "text": transcript, "duration_s": 6.0, "cost_inr": 0.01})

    async def translate_text(text: str, target_language: str) -> str:
        log.append("translate_text")
        return json.dumps({"ok": True, "translation": HINDI, "truncated": False, "cost_inr": 0.001})

    tools = [StructuredTool.from_function(coroutine=f, name=f.__name__, description=f.__name__) for f in (speak_text, transcribe_audio, translate_text)]
    return tools, log


async def run_graph(tmp_path, replies, tools, **kw):
    rec = RunRecorder(tmp_path)
    llm = ScriptedLLM(replies)
    graph = build_graph(llm, tools, rec, **{"max_steps": 10, "max_cost_inr": 5.0, **kw})
    final = await graph.ainvoke(initial_state("task"), {"recursion_limit": 60})
    return final, rec, llm


def trace(rec):
    return [json.loads(line) for line in rec.trace_path.read_text(encoding="utf-8").splitlines()]


def test_compare_spoken_detects_omission_and_repetition():
    assert compare_spoken(HINDI, HINDI)["passed"]
    omitted = compare_spoken(HINDI * 3, HINDI)
    assert not omitted["passed"] and "omits" in omitted["problems"][0]
    repeated = compare_spoken(HINDI, HINDI * 3)
    assert not repeated["passed"] and "repeats" in repeated["problems"][0]
    assert not compare_spoken(HINDI, "")["passed"]


def test_check_translation_coverage():
    src = "| a | b |\n| 1 | 2 |\n\nHello world"
    assert check_translation(src, "| क | ख |\n| १ | २ |\n\nनमस्ते दुनिया", "hi", truncated=False)["passed"]
    assert any("truncated" in p for p in check_translation("Hi", HINDI, "hi", truncated=True)["problems"])
    assert any("table rows" in p for p in check_translation(src, "नमस्ते\n\nदुनिया\nफिर", "hi", truncated=False)["problems"])
    assert any("script" in p for p in check_translation("Hello", "Hello there", "ta", truncated=False)["problems"])


async def test_audio_and_translation_are_verified_before_finishing(tmp_path):
    tools, log = make_tools()
    replies = [call("translate_text", text="Hello world, how are you today", target_language="hi"), call("speak_text", text=HINDI, output_path="a.wav", language="hi"), AIMessage(content="Done")]
    final, rec, _ = await run_graph(tmp_path, replies, tools)
    assert final["status"] == "completed" and log == ["translate_text", "speak_text", "transcribe_audio"]
    assert [v["status"] for v in final["verifications"]] == ["passed", "passed"]
    assert [t["kind"] for t in trace(rec)] == ["plan", "tool", "plan", "tool", "plan", "verify", "verify"]
    assert all({"latency_s", "cost_inr"} <= t.keys() for t in trace(rec) if t["kind"] in ("tool", "verify"))


async def test_failed_verification_gives_one_repair_round_then_reports(tmp_path):
    tools, log = make_tools(transcript="कुछ और")
    speak = call("speak_text", text=HINDI, output_path="a.wav", language="hi")
    speak2 = call("speak_text", text=HINDI, output_path="b.wav", language="hi")
    final, rec, llm = await run_graph(tmp_path, [speak, AIMessage(content="Done"), speak2, AIMessage(content="Audio could not be verified")], tools)
    assert final["status"] == "completed"
    assert log.count("speak_text") == 2 and final["verify_rounds"] == 1
    assert [v["status"] for v in final["verifications"]] == ["failed", "failed"]  # second failure is reported, not retried again
    assert "verification found problems" in str(llm.seen[2][-1].content)
    rec.finish(status="completed", task="t", answer="x", trace_url=None, max_steps=10, max_cost_inr=5.0, verifications=final["verifications"])
    assert json.loads((tmp_path / "report.json").read_text())["verification_summary"]["failed"] == 2


async def test_cost_budget_refuses_a_call_before_spending(tmp_path):
    tools, log = make_tools()
    long_text = "क" * 2000  # about Rs 1.20
    final, rec, _ = await run_graph(tmp_path, [call("speak_text", text=long_text, output_path="a.wav")], tools, max_cost_inr=0.5)
    assert final["status"] == "budget_exhausted" and "speak_text" not in log and final["cost_inr"] == 0
    assert "cost budget" in final["messages"][-1].content and "What was done" in final["messages"][-1].content
    assert json.loads(trace(rec)[1]["result_summary"])["error"]["code"] == "budget_exceeded"


async def test_cost_budget_stops_after_overspend_and_lists_unverified_outputs(tmp_path):
    tools, log = make_tools()
    replies = [call("translate_text", text="Hello world, how are you today", target_language="hi"), call("translate_text", text="x", target_language="hi")]
    final, _, _ = await run_graph(tmp_path, replies, tools, max_cost_inr=0.001)  # the first call spends the whole budget
    assert final["status"] == "budget_exhausted" and log == ["translate_text"]
    assert "not verified" in final["messages"][-1].content


async def test_step_budget_stops_cleanly(tmp_path):
    tools, log = make_tools()
    replies = [call("translate_text", text=str(i), target_language="hi") for i in range(5)]
    final, rec, _ = await run_graph(tmp_path, replies, tools, max_steps=2)
    assert final["status"] == "budget_exhausted" and final["llm_turns"] == 2 and log == ["translate_text"]
    assert "step budget" in final["messages"][-1].content


async def test_per_call_timeout_becomes_a_retryable_error(tmp_path):
    tools, _ = make_tools(delay=1.0)
    replies = [call("speak_text", text=HINDI, output_path="a.wav"), AIMessage(content="could not speak")]
    final, rec, _ = await run_graph(tmp_path, replies, tools, call_timeout_s=0.05)
    err = json.loads(trace(rec)[1]["result_summary"])["error"]
    assert err["code"] == "timeout" and err["retryable"] and final["status"] == "completed" and final["verifications"] == []


async def test_unknown_tool_does_not_crash_the_run(tmp_path):
    tools, _ = make_tools()
    final, rec, _ = await run_graph(tmp_path, [call("make_video"), AIMessage(content="impossible")], tools)
    assert final["status"] == "completed" and "unknown_tool" in trace(rec)[1]["result_summary"]


async def test_report_trace_summary_and_result_files(tmp_path):
    tools, _ = make_tools()
    final, rec, _ = await run_graph(tmp_path, [call("translate_text", text="Hello world, how are you today", target_language="hi"), AIMessage(content="Done")], tools)
    (rec.artifacts_dir / "x.md").write_text("hi", encoding="utf-8")
    rec.finish(status=final["status"], task="t", answer="Done", trace_url=None, max_steps=10, max_cost_inr=5.0, verifications=final["verifications"], call_timeout_s=30)
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed" and report["artifacts"] == ["artifacts/x.md"] and report["budgets"]["call_timeout_s"] == 30
    assert report["verification_summary"] == {"passed": 1, "failed": 0, "skipped": 0} and report["total_cost_inr"] > 0
    assert trace(rec)[-1]["kind"] == "summary"
    assert "## Verification" in (tmp_path / "result.md").read_text(encoding="utf-8")
