"""End-to-end agent runs with a scripted LLM and the fake Bodhan API."""
import json
from pathlib import Path

import pytest
from conftest import make_speech_file

from bodhan_agent.agent import Agent
from bodhan_agent.llm import Reply, ToolCallReq
from bodhan_mcp.costs import tracker


def call(name, **args):
    return ToolCallReq(f"id-{name}-{abs(hash(json.dumps(args, sort_keys=True))) % 10**6}", name, json.dumps(args))


class ScriptedLLM:
    """Each step is a function(messages) -> list of tool calls (or a str for plain text)."""

    def __init__(self, steps):
        self.steps, self.i, self.seen = steps, 0, []

    async def chat(self, messages, tools=None):
        self.seen.append([dict(m) for m in messages])
        step = self.steps[min(self.i, len(self.steps) - 1)]
        self.i += 1
        out = step(messages)
        if isinstance(out, str):
            return Reply(out)
        return Reply("", out)


def last_results(messages):
    """JSON of the tool messages that answered the previous assistant turn."""
    out = []
    for m in reversed(messages):
        if m["role"] == "tool":
            out.append(json.loads(m["content"]))
        elif m["role"] == "assistant":
            break
    return out[::-1]


@pytest.fixture
def pdf(tmp_path):
    from PIL import Image
    d = tmp_path / "in"
    d.mkdir()
    ims = [Image.new("RGB", (300, 400), c) for c in ("white", "beige")]
    ims[0].save(d / "chapter.pdf", save_all=True, append_images=ims[1:])
    return d


async def run(task, files, tmp_path, llm, **kw):
    agent = Agent(task, [str(f) for f in files], tmp_path / "out", llm, **kw)
    report = await agent.run()
    return agent, report


async def test_multistep_task_with_automatic_verification(fake, pdf, tmp_path):
    fake.asr_text = lambda dur, lang, i: fake.last_tts_text
    out = tmp_path / "out" / "artifacts"
    summary = "Lines and angles are the basics of geometry. A right angle is ninety degrees. Parallel lines never meet."

    def s_ocr(m):
        return [call("ocr_document", input_path=str(pdf / "chapter.pdf"))]

    def s_write(m):
        return [call("write_text_file", filename="summary.en.txt", content=summary)]

    def s_translate(m):
        return [call("translate_text", target_language="Tamil", input_path=str(out / "summary.en.txt"),
                     output_path="summary.ta.txt")]

    def s_speak(m):
        return [call("synthesize_speech", language="Tamil", input_path=str(out / "summary.ta.txt"),
                     output_path="summary.ta.mp3")]

    def s_finish(m):
        return [call("finish", status="completed", summary="Made a Tamil audio summary (AI-generated voice).",
                     artifacts=[str(out / "summary.ta.mp3"), str(out / "summary.ta.txt")],
                     assumptions=["Chapter is in English", "Female voice chosen"])]

    llm = ScriptedLLM([lambda m: "1. OCR 2. summarise 3. translate 4. speak", s_ocr, s_write, s_translate, s_speak, s_finish])
    agent, rep = await run("Make a Tamil audio summary of this chapter", [pdf], tmp_path, llm)

    assert rep["status"] == "completed", rep
    assert (out / "summary.ta.mp3").exists() and (out / "summary.ta.txt").exists()
    kinds = {(v["kind"], v["ok"]) for v in rep["verification"]}
    assert ("speech", True) in kinds and ("translation", True) in kinds        # verified before finishing
    assert rep["targets"]["all_outputs_verified"] and rep["targets"]["duplicate_model_requests"] == 0
    assert 0 < rep["total_cost_inr"] <= 20 and rep["total_cost_inr"] == pytest.approx(tracker.total, rel=0.01)
    assert rep["cost_by_tool_inr"]["ocr_document"] == pytest.approx(0.4)
    # all four output files exist and tell the story
    root = tmp_path / "out"
    result = (root / "result.md").read_text(encoding="utf-8")
    assert "COMPLETED" in result and "AI-generated" in result and "Female voice chosen" in result
    trace = [json.loads(l) for l in (root / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    types = [t["type"] for t in trace]
    assert types[0] == "preflight" and types[1] == "plan"
    assert types.count("tool_call") == 4 and "verify" in types and "finish" in types and types[-1] == "done"
    tc = [t for t in trace if t["type"] == "tool_call"]
    assert all({"tool", "arguments", "result", "latency_s", "cost_inr", "cumulative_cost_inr"} <= set(t) for t in tc)


async def test_impossible_task_ends_honestly_with_no_output_and_no_spend(fake, pdf, tmp_path):
    video = tmp_path / "in" / "clip.mp4"
    video.write_bytes(b"x")
    llm = ScriptedLLM([lambda m: "French is not supported.",
                       lambda m: [call("finish", status="impossible",
                                       summary="No Bodhan model supports French, so I cannot translate this video.",
                                       artifacts=["/tmp/should-be-ignored.mp4"])]])
    agent, rep = await run("Translate this video into French", [video], tmp_path, llm)
    assert rep["status"] == "impossible" and rep["artifacts"] == [] and rep["total_cost_inr"] == 0
    assert fake.calls == []
    assert "IMPOSSIBLE" in (tmp_path / "out" / "result.md").read_text(encoding="utf-8")
    assert not list((tmp_path / "out" / "artifacts").glob("*.mp3"))


async def test_missing_input_file_is_visible_to_the_planner(fake, tmp_path):
    seen = {}

    def plan(m):
        seen["user"] = m[1]["content"]
        return "The file is missing."
    llm = ScriptedLLM([plan, lambda m: [call("finish", status="impossible", summary="The audio file was not provided.")]])
    _, rep = await run("Transcribe this", [tmp_path / "ghost.wav"], tmp_path, llm)
    assert "MISSING" in seen["user"] and rep["status"] == "impossible"


async def test_budget_stops_cleanly_before_overspending(fake, tmp_path):
    long_text = "This is a long sentence that costs money to speak aloud. " * 20

    def write(m):
        return [call("write_text_file", filename="t.txt", content=long_text)]

    def speak(m):
        return [call("synthesize_speech", language="Tamil", input_path=str(tmp_path / "out" / "artifacts" / "t.txt"))]
    llm = ScriptedLLM([lambda m: "plan", write, speak, lambda m: "unreachable"])
    _, rep = await run("Read this aloud", [], tmp_path, llm, max_cost_inr=0.02)
    assert rep["status"] == "partial" and rep["stop_reason"] == "max_cost"
    assert rep["total_cost_inr"] <= 0.02 and fake.count("/v1/audio/speech") == 0
    assert "budget" in (tmp_path / "out" / "result.md").read_text(encoding="utf-8").lower()
    assert (tmp_path / "out" / "report.json").exists() and (tmp_path / "out" / "trace.jsonl").exists()


async def test_step_budget_and_time_budget(fake, tmp_path):
    llm = ScriptedLLM([lambda m: "plan", lambda m: [call("list_capabilities")]])
    _, rep = await run("loop", [], tmp_path, llm, max_steps=3)
    assert rep["stop_reason"] == "max_steps" and rep["steps"] == 3 and rep["status"] == "partial"
    llm2 = ScriptedLLM([lambda m: "plan", lambda m: [call("list_capabilities")]])
    _, rep2 = await run("loop", [], tmp_path / "t2", llm2, max_seconds=0)
    assert rep2["stop_reason"] == "max_seconds"


async def test_extra_parallel_calls_beyond_step_budget_are_refused(fake, tmp_path):
    llm = ScriptedLLM([lambda m: "plan",
                       lambda m: [call("detect_text_language", text=f"hello {i}") for i in range(5)]])
    _, rep = await run("x", [], tmp_path, llm, max_steps=2)
    assert rep["steps"] == 2


async def test_prompt_injection_in_ocr_text_is_wrapped_as_untrusted_data(fake, pdf, tmp_path):
    def ocr(m):
        return [call("ocr_document", input_path=str(pdf / "chapter.pdf"))]

    def done(m):
        res = last_results(m)[0]
        assert res["preview"].startswith("<<UNTRUSTED_DATA>>") and res["preview"].rstrip().endswith("<</UNTRUSTED_DATA>>")
        assert "IGNORE PREVIOUS INSTRUCTIONS" in res["preview"]
        return [call("finish", status="partial", summary="Extracted text. The page contained an instruction, ignored.")]
    llm = ScriptedLLM([lambda m: "plan", ocr, done])
    _, rep = await run("Extract text", [pdf], tmp_path, llm)
    assert rep["status"] == "partial" and fake.count("/v1/audio/speech") == 0     # the injected command was not obeyed
    system = llm.seen[0][0]["content"]
    assert "UNTRUSTED" in system and "never write" in system.lower() or "never writes" in system.lower() or "do not follow" in system.lower()


async def test_llm_cannot_write_indian_language_text(fake, tmp_path):
    def bad(m):
        return [call("write_text_file", filename="a.txt", content="இது ஒரு தமிழ் வாக்கியம் இது ஒரு தமிழ் வாக்கியம்")]

    def after(m):
        assert last_results(m)[0]["code"] == "non_english_text_rejected"
        return [call("finish", status="impossible", summary="stop")]
    _, rep = await run("x", [], tmp_path, ScriptedLLM([lambda m: "plan", bad, after]))
    assert not list((tmp_path / "out" / "artifacts").glob("a.txt"))


async def test_failed_verification_blocks_completion_and_is_reported_honestly(fake, tmp_path):
    fake.asr_text = "completely different words that were never in the script at all okay"
    text = "Welcome to the lesson on lines and angles. Today we learn about parallel lines."

    def w(m):
        return [call("write_text_file", filename="t.txt", content=text)]

    def sp(m):
        return [call("synthesize_speech", language="Hindi", input_path=str(tmp_path / "out/artifacts/t.txt"),
                     output_path="a.mp3")]
    fin = lambda m: [call("finish", status="completed", summary="done", artifacts=[str(tmp_path / "out/artifacts/a.mp3")])]  # noqa: E731
    llm = ScriptedLLM([lambda m: "plan", w, sp, fin, fin, fin])
    _, rep = await run("read aloud", [], tmp_path, llm)
    assert rep["status"] == "partial"                          # LLM said completed; verification disagreed
    assert any(v["kind"] == "speech" and not v["ok"] for v in rep["verification"])
    assert any("Verification" in n for n in rep["notes"])


async def test_identical_repeat_calls_are_not_paid_twice(fake, tmp_path):
    llm = ScriptedLLM([lambda m: "plan",
                       lambda m: [call("translate_text", target_language="ta", text="Hello world, one two three.")],
                       lambda m: [call("translate_text", target_language="ta", text="Hello world, one two three.")],
                       lambda m: [call("finish", status="partial", summary="x")]])
    _, rep = await run("x", [], tmp_path, llm)
    forward = [c for c in fake.calls if c["path"] == "/translate" and c["target"] == "ta"]
    assert len(forward) == 1 and rep["steps"] == 3          # (the 2nd /translate call is the back-translation check)


async def test_runs_through_the_stdio_mcp_server_too(fake, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello", encoding="utf-8")

    def inspect(m):
        return [call("detect_text_language", text="இது ஒரு சோதனை"), call("list_capabilities")]

    def fin(m):
        res = last_results(m)
        assert res[0]["language"] == "ta" and "fr" not in res[1]["translate_languages"]
        return [call("finish", status="impossible", summary="checked over MCP")]
    agent, rep = await run("x", [f], tmp_path, ScriptedLLM([lambda m: "plan", inspect, fin]), use_mcp=True)
    assert rep["backend"] == "mcp-stdio" and rep["status"] == "impossible"
