"""End-to-end agent runs with a scripted LLM and a fake Bodhan client: no network, no spend."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from openai.types.chat import ChatCompletion
from test_tools import FakeBodhan

from bodhan_agent.__main__ import run


class ScriptedLLM:
    """Stands in for AsyncOpenAI: replays a list of tool calls, one per LLM turn, and records what it was sent."""

    def __init__(self, calls: list[tuple[str, dict]]):
        self.calls, self.seen = list(calls), []
        self.chat = self
        self.completions = self

    async def create(self, model, messages, tools):
        self.seen.append(messages)
        name, args = self.calls.pop(0) if self.calls else ("read_text", {"why": "loop", "path": "x"})
        return ChatCompletion.model_validate({
            "id": "x", "object": "chat.completion", "created": 0, "model": model,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"c{len(self.seen)}", "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}]})


def agent_run(tmp_path, calls, task="Read this paragraph aloud in Bengali", max_steps=30):
    inputs = tmp_path / "in"
    inputs.mkdir(exist_ok=True)
    (inputs / "paragraph.txt").write_text("Hello class. Water is precious.")
    mp3 = tmp_path / "beep.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=0.3", str(mp3)], check=True)
    args = argparse.Namespace(task=task, files=str(inputs), out=str(tmp_path / "out"), max_steps=max_steps,
                              max_cost_inr=20.0, max_seconds=60, call_timeout=10)
    llm = ScriptedLLM(calls)
    report = asyncio.run(run(args, llm=llm, bodhan=FakeBodhan(mp3.read_bytes())))
    trace = [json.loads(line) for line in (tmp_path / "out" / "trace.jsonl").read_text().splitlines()]
    return report, trace, llm


def test_multi_step_task_end_to_end(tmp_path):
    art = tmp_path / "out" / "artifacts"
    report, trace, llm = agent_run(tmp_path, [
        ("translate_text", {"why": "Bengali text first", "path": str(tmp_path / "in" / "paragraph.txt"),
                            "target_language": "bn"}),
        ("synthesize_speech", {"why": "read it aloud", "path": str(art / "paragraph.bn.md"), "language": "bn"}),
        ("finish", {"why": "done", "status": "completed", "answer": "Bengali audio is ready.",
                    "assumptions": ["female voice by default"], "artifacts": [str(art / "paragraph.bn.mp3")]}),
    ])
    assert report["status"] == "completed" and report["steps"] == 3
    assert [t["tool"] for t in trace] == ["translate_text", "synthesize_speech", "finish"]
    assert trace[0]["plan"] == "Bengali text first" and "why" not in trace[0]["args"]
    assert report["artifacts"] == [str((art / "paragraph.bn.mp3").resolve())]
    result = (tmp_path / "out" / "result.md").read_text()
    assert "Bengali audio is ready." in result and "AI-generated" in result
    assert [v["tool"] for v in report["verification"]] == ["translate_text", "synthesize_speech"]
    assert all(v["ok"] for v in report["verification"]) and "## Checks" in result
    first_prompt = llm.seen[0][1]["content"]
    assert "paragraph.txt" in first_prompt and "Supported languages" in first_prompt  # manifest + language list


def test_llm_written_indic_text_is_refused(tmp_path):
    report, trace, _ = agent_run(tmp_path, [
        ("synthesize_speech", {"why": "speak", "text": "নমস্কার", "language": "bn"}),
        ("finish", {"why": "x", "status": "partial", "answer": "Could not."}),
    ])
    assert trace[0]["error"] == "indic_text_in_arguments" and report["cost_inr"] == 0


def test_step_budget_stops_cleanly(tmp_path):
    report, trace, _ = agent_run(tmp_path, [], max_steps=3)  # LLM never finishes
    assert report["status"] == "stopped" and len(trace) == 3 and "step budget" in report["stop_reason"]
    assert (tmp_path / "out" / "result.md").exists()


def test_claimed_but_missing_artifact_is_not_reported_as_output(tmp_path):
    report, _, _ = agent_run(tmp_path, [
        ("finish", {"why": "x", "status": "completed", "answer": "Done.", "artifacts": ["/nope/fake.mp3"]})])
    assert report["artifacts"] == [] and report["missing_artifacts"] == ["/nope/fake.mp3"]


def test_impossible_task_leaves_no_files(tmp_path):
    report, _, _ = agent_run(tmp_path, [
        ("translate_text", {"why": "x", "path": str(tmp_path / "in" / "paragraph.txt"), "target_language": "hi"}),
        ("finish", {"why": "French unsupported", "status": "impossible", "answer": "French is not supported."}),
    ], task="Translate into French")
    assert report["status"] == "impossible" and not list((tmp_path / "out" / "artifacts").iterdir())


def test_missing_input_fails_fast_without_llm(tmp_path):
    args = argparse.Namespace(task="t", files=str(tmp_path / "nope"), out=str(tmp_path / "out"), max_steps=5,
                              max_cost_inr=1.0, max_seconds=10, call_timeout=5)
    llm = ScriptedLLM([])
    report = asyncio.run(run(args, llm=llm, bodhan=FakeBodhan()))
    assert report["status"] == "impossible" and llm.seen == []
    assert "does not exist" in (tmp_path / "out" / "result.md").read_text()


def test_length_target_and_failed_check_downgrades_completed(tmp_path):
    art = tmp_path / "out" / "artifacts"
    report, trace, _ = agent_run(tmp_path, [
        ("write_file", {"why": "draft", "filename": "s.en.md", "content": "word " * 50, "speech_language": "ta"}),
        ("translate_text", {"why": "Bengali", "path": str(tmp_path / "in" / "paragraph.txt"), "target_language": "bn"}),
        ("synthesize_speech", {"why": "speak", "path": str(art / "paragraph.bn.md"), "language": "bn"}),
        ("finish", {"why": "done", "status": "completed", "answer": "Done.", "artifacts": [str(art / "paragraph.bn.mp3")]}),
    ], task="Make a two-minute Bengali audio summary")
    assert trace[0]["result"]["length_check"].startswith("TOO SHORT: 50 words, about 180 wanted")
    assert "length_vs_request" in trace[2]["result"]  # the 0.3 s fake audio is far from 120 s
    assert report["status"] == "partial" and "Marked partial by the output checks" in (tmp_path / "out" / "result.md").read_text()
