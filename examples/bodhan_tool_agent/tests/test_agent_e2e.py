"""End-to-end agent runs with a scripted planner LLM and the fake Bodhan API.

The scripted LLM stands in for a real model so the loop, budgets, trace, verification and
output layout are tested deterministically.
"""

import json
from pathlib import Path

import httpx
import pytest

from bodhan_agent import cli
from bodhan_mcp import client as client_mod


class ScriptedLLM:
    """Each step is a function(last_tool_results) -> list of (tool_name, args)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen_tools = None
        self.n = 0

    def __call__(self, messages, tools, timeout):
        self.seen_tools = {t["function"]["name"] for t in tools}
        results = []
        for m in reversed(messages):
            if m["role"] != "tool":
                break
            results.insert(0, json.loads(m["content"]))
        calls = self.steps.pop(0)(results)
        self.n += 1
        return {"content": None, "tool_calls": [
            {"id": f"c{self.n}_{i}", "name": n, "arguments": json.dumps(a, ensure_ascii=False)} for i, (n, a) in enumerate(calls)]}


@pytest.fixture
def offline(monkeypatch, fake, tmp_path):
    monkeypatch.setenv("BODHAN_API_KEY", "test-key-placeholder")
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(tmp_path / "cache"))
    real = client_mod.BodhanClient.__init__

    def init(self, settings=None, transport=None):
        real(self, settings, transport=httpx.MockTransport(fake))

    monkeypatch.setattr(client_mod.BodhanClient, "__init__", init)
    return fake


def _run(tmp_path, task, files, llm, *extra):
    out = tmp_path / "out"
    code = cli.main(["--task", task, "--files", *map(str, files), "--out", str(out), *extra], chat=llm)
    return code, out, json.loads((out / "report.json").read_text())


def test_hindi_voice_note_to_english_subtitles(tmp_path, offline, media_dir):
    llm = ScriptedLLM([
        lambda r: [("record_plan", {"steps": ["transcribe", "subtitles in en"], "assumptions": ["spoken language is Hindi"]})],
        lambda r: [("transcribe_audio", {"path": str(media_dir / "voice_note.wav")})],
        lambda r: [("create_subtitles", {"transcript_path": r[0]["transcript_json"], "target_language": "en"})],
        lambda r: [("finish", {"status": "completed", "answer": "English subtitles created.", "artifacts": [r[0]["path"]]})],
    ])
    code, out, rep = _run(tmp_path, "Transcribe this Hindi voice note and give me English subtitles",
                          [media_dir / "voice_note.wav"], llm)
    assert code == 0 and rep["status"] == "completed"
    assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document", "finish"} <= llm.seen_tools
    assert (out / "result.md").exists() and Path(rep["artifacts"][0]).suffix == ".srt"
    trace = [json.loads(line) for line in (out / "trace.jsonl").read_text().splitlines()]
    tool_steps = [t for t in trace if t["type"] == "tool"]
    assert [t["tool"] for t in tool_steps] == ["transcribe_audio", "create_subtitles"]
    assert all({"arguments", "latency_ms", "cost_inr", "result_summary"} <= t.keys() for t in tool_steps)
    assert rep["cost_inr"]["within_budget"] and rep["steps"] == 2


def test_tamil_audio_summary_multi_step(tmp_path, offline, media_dir):
    """OCR -> English summary (LLM, English only) -> translate -> speak -> verified mp3."""
    llm = ScriptedLLM([
        lambda r: [("record_plan", {"steps": ["ocr", "summarise in English", "translate ta", "speak"]})],
        lambda r: [("ocr_document", {"path": str(media_dir / "chapter.pdf")})],
        lambda r: [("read_text", {"path": r[0]["path"]})],
        lambda r: [("write_text", {"filename": "summary.en.md", "content": "The chapter explains angles. A right angle is 90 degrees."})],
        lambda r: [("translate_text", {"target_language": "ta", "text_path": r[0]["path"]})],
        lambda r: [("synthesize_speech", {"language": "ta", "text_path": r[0]["path"]})],
        lambda r: [("finish", {"status": "completed", "answer": "Done", "artifacts": [r[0]["path"]]})],
    ])
    code, out, rep = _run(tmp_path, "Make a two-minute Tamil audio summary of this chapter", [media_dir], llm)
    assert code == 0 and rep["status"] == "completed", rep["verification"]
    audio = [c for c in rep["verification"]["checks"] if c["check"] == "audio back-transcription"]
    assert audio and audio[0]["passed"]
    assert Path(rep["artifacts"][0]).name.endswith(".ta.mp3")


def test_impossible_language_fails_honestly(tmp_path, offline, media_dir):
    llm = ScriptedLLM([
        lambda r: [("record_plan", {"steps": ["check French support"]})],
        lambda r: [("translate_text", {"target_language": "fr", "text": "hello"})],
        lambda r: [("finish", {"status": "not_possible", "answer": f"French is not supported: {r[0]['error']['code']}"})],
    ])
    code, out, rep = _run(tmp_path, "Translate this video into French", [media_dir / "clip_hi.wav"], llm)
    assert rep["status"] == "not_possible" and code == 0
    assert "unsupported_language" in (out / "result.md").read_text()
    assert rep["verification"]["artifacts"] == []


def test_budget_blocks_expensive_call(tmp_path, offline, media_dir):
    long_text = media_dir / "long.txt"
    long_text.write_text("Plants need light. " * 400)
    llm = ScriptedLLM([
        lambda r: [("record_plan", {"steps": ["translate", "speak"]})],
        lambda r: [("translate_text", {"target_language": "bn", "text_path": str(long_text)})],
        lambda r: [("synthesize_speech", {"language": "bn", "text_path": r[0]["path"]})],
        lambda r: [("finish", {"status": "not_possible", "answer": r[0]["error"]["message"]})],
    ])
    code, out, rep = _run(tmp_path, "Read this aloud in Bengali", [long_text], llm, "--max-cost-inr", "1")
    assert offline.count("/v1/audio/speech") == 0
    assert rep["cost_inr"]["total"] <= 1 and "budget" in (out / "result.md").read_text()


def test_claimed_success_without_artifact_is_downgraded(tmp_path, offline, media_dir):
    llm = ScriptedLLM([
        lambda r: [("record_plan", {"steps": ["x"]})],
        lambda r: [("finish", {"status": "completed", "answer": "Here is your audio", "artifacts": ["made_up.mp3"]})],
    ])
    code, out, rep = _run(tmp_path, "Read this aloud", [media_dir / "paragraph.txt"], llm)
    assert rep["status"] == "partial" and code == 1 and rep["verification"]["missing_artifacts"] == ["made_up.mp3"]


def test_tools_require_a_plan_and_bad_args_do_not_crash(tmp_path, offline, media_dir):
    llm = ScriptedLLM([
        lambda r: [("transcribe_audio", {"path": "x.wav"})],
        lambda r: [("record_plan", {"steps": ["go"]})] if r[0]["error"]["code"] == "plan_required" else [],
        lambda r: [("transcribe_audio", {"path": 123, "bogus": True}), ("ocr_document", {"path": "missing.png"})],
        lambda r: [("finish", {"status": "not_possible", "answer": json.dumps([x["error"]["code"] for x in r])})],
    ])
    code, out, rep = _run(tmp_path, "Transcribe", [], llm)
    assert rep["status"] == "not_possible"
    assert "file_not_found" in (out / "result.md").read_text()


def test_missing_inputs_end_honestly_without_llm(tmp_path, monkeypatch):
    """Decided in code: no LLM call, no model call, no artifacts, a clear explanation."""
    import json as _json

    from bodhan_agent.cli import main
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    out = tmp_path / "out"
    rc = main(["--task", "Summarise the attached report in Hindi", "--files", str(tmp_path / "nope.pdf"),
               "--out", str(out)])
    rep = _json.loads((out / "report.json").read_text())
    assert rc == 0 and rep["status"] == "not_possible" and rep["artifacts"] == []
    assert "nope.pdf" in (out / "result.md").read_text()
