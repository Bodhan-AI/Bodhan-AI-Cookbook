"""Tests for the end-to-end agent execution loop, tracing, and reporting (Slice 11)."""

import json
from pathlib import Path

import httpx
import pytest

from bodhan_agent.loop import run_task
from bodhan_mcp.clients.base import BaseBodhanClient
from bodhan_mcp.clients.bodhan import BodhanClient


@pytest.mark.asyncio
async def test_single_step_task_writes_all_four_outputs(short_wav: Path, tmp_path: Path):
    """Verify one single-step task end-to-end through loop.py writes all four required outputs."""
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    # Copy sample WAV to inputs directory
    test_wav = inputs_dir / "audio_sample.wav"
    test_wav.write_bytes(short_wav.read_bytes())

    out_dir = tmp_path / "out"

    # Mock Bodhan HTTP
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "नमस्ते दुनिया",
                "language": "hi",
                "language_confidence": 0.95,
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "नमस्ते दुनिया"},
                ],
            },
        )

    transport = httpx.MockTransport(mock_handler)
    base_client = BaseBodhanClient(base_url="https://mock.bodhan.ai/v1")
    base_client._client = httpx.AsyncClient(transport=transport)
    bodhan = BodhanClient(base_client=base_client)

    result = await run_task(
        task="Transcribe audio_sample.wav to Hindi text",
        files_dir=inputs_dir,
        out_dir=out_dir,
        max_cost_inr=5.0,
        tool_client=bodhan,
    )

    assert result["status"] == "success"

    # 1. out/result.md exists and contains markdown
    result_md = out_dir / "result.md"
    assert result_md.exists()
    content_md = result_md.read_text(encoding="utf-8")
    assert "नमस्ते दुनिया" in content_md
    assert "## Summary of Actions" in content_md

    # 2. out/report.json exists and conforms to schema
    report_json = out_dir / "report.json"
    assert report_json.exists()
    report_data = json.loads(report_json.read_text(encoding="utf-8"))
    assert report_data["status"] == "success"
    assert len(report_data["inputs"]) >= 1
    assert len(report_data["steps"]) >= 1
    assert report_data["total_cost_inr"] > 0.0
    assert report_data["total_time_s"] >= 0.0

    # 3. out/trace.jsonl exists and has monotonically increasing steps
    trace_jsonl = out_dir / "trace.jsonl"
    assert trace_jsonl.exists()
    trace_lines = [json.loads(line) for line in trace_jsonl.read_text(encoding="utf-8").splitlines() if line]
    assert len(trace_lines) >= 4  # resolve, preflight, plan, tool_call, final
    for i in range(len(trace_lines)):
        assert trace_lines[i]["step"] == i + 1

    # 4. out/artifacts/ directory contains artifacts
    artifacts = list((out_dir / "artifacts").iterdir())
    assert len(artifacts) >= 1
    await base_client.close()


@pytest.mark.asyncio
async def test_agent_preflight_refusal(short_wav: Path, tmp_path: Path):
    """Verify loop refuses immediately at pre-flight stage if budget is too small."""
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    (inputs_dir / "podcast.wav").write_bytes(short_wav.read_bytes())
    out_dir = tmp_path / "out_refusal"

    res = await run_task(
        task="Transcribe podcast.wav and synthesize an audio version",
        files_dir=inputs_dir,
        out_dir=out_dir,
        max_cost_inr=0.0001,  # Impossible budget
    )

    assert res["status"] == "budget_exhausted"
    # Zero model calls and zero spend
    assert res["cost_inr"] == 0.0

    # Confirm report.json and result.md record preflight refusal cleanly
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "budget_exhausted"
    assert report["steps"] == []


@pytest.mark.asyncio
async def test_agent_missing_file_impossible_task(tmp_path: Path):
    """Verify loop refuses impossible task with missing explicit file and produces zero artifacts."""
    inputs_dir = tmp_path / "empty_inputs"
    inputs_dir.mkdir()
    out_dir = tmp_path / "out_impossible"

    res = await run_task(
        task="Please transcribe missing_speech.wav",
        files_dir=inputs_dir,
        out_dir=out_dir,
    )

    assert res["status"] == "impossible"
    assert res["cost_inr"] == 0.0
    # Invariant: zero artifacts produced per Constraint C8
    artifacts = list((out_dir / "artifacts").iterdir())
    assert len(artifacts) == 0
