"""Agent guards, and one end-to-end run: real agent, real MCP server subprocess, fake Bodhan and fake LLM over HTTP."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bodhan_agent.agent import Run, parse_args
from fakes import FakeBodhan, FakeServer


@pytest.fixture
def run(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "note.txt").write_text("hello")
    r = Run(parse_args(["--task", "t", "--files", str(tmp_path / "in"), "--out", str(tmp_path / "out")]))
    r.mcp_names = {"translate_text", "synthesize_speech"}
    return r


def _call(name, args):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def test_guard_blocks_llm_written_indic_text(run):
    import asyncio

    out = asyncio.run(run.dispatch(_call("synthesize_speech", {"language": "hi", "text": "नमस्ते दुनिया"})))
    assert "rejected_by_agent" in out
    run.seen = "transcript: नमस्ते दुनिया"  # the same text, once a tool produced it, may be passed on
    assert run.unproven_indic("नमस्ते दुनिया") == []


def test_guard_blocks_paths_outside_sandbox(run):
    import asyncio

    out = asyncio.run(run.dispatch(_call("translate_text", {"path": "/etc/passwd", "target_language": "hi"})))
    assert "rejected_by_agent" in out
    out = asyncio.run(run.dispatch(_call("translate_text", {"text": "hi", "target_language": "hi", "output_path": "/tmp/x.md"})))
    assert "rejected_by_agent" in out


def test_finish_gate_rejects_unverified_audio_once(run):
    (run.art / "a.mp3").write_bytes(b"x")
    assert run.try_finish({"status": "success", "answer": "done"}).startswith("finish rejected")
    run.try_finish({"status": "success", "answer": "done"})
    assert run.finish["status"] == "partial" and run.errors


def test_end_to_end_transcribe_then_translate(tmp_path, speech_wav):
    srv = FakeServer(FakeBodhan())
    try:
        env = {
            **os.environ,
            "LLM_BASE_URL": f"{srv.url}/v1",
            "LLM_MODEL": "fake",
            "LLM_API_KEY": "test-key",
            "BODHAN_BASE_URL": srv.url,
            "BODHAN_API_KEY": "test-key",
            "BODHAN_CACHE_DIR": str(tmp_path / "cache"),
        }
        out = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, "-m", "bodhan_agent", "--task", "Transcribe this and give me English", "--files", str(speech_wav), "--out", str(out)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads((out / "report.json").read_text())
        assert report["status"] == "success", report
        assert report["artifacts"] == ["transcript_en.md"] and report["verification"][0]["verification"]["passed"]
        assert report["total_cost_inr"] > 0 and report["steps"] == 4
        kinds = [json.loads(line)["kind"] for line in (out / "trace.jsonl").read_text().splitlines()]
        assert kinds[0] == "start" and "plan" in kinds and kinds.count("tool") == 3 and kinds[-1] == "end"
        assert "transcript_en.md" in (out / "result.md").read_text()
    finally:
        srv.close()
