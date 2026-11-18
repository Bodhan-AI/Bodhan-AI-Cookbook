"""End-to-end integration tests for multi-step language pipelines with mocked/cached responses."""

import json
from pathlib import Path

import pytest
from PIL import Image

from bodhan_agent.models import ExecutionStatus
from bodhan_agent.runner import AgentRunner
from bodhan_mcp.cache import cache
from bodhan_mcp.server import create_mcp_server, dispatch_tool_call
from tests.test_chunking_and_limits import create_synthetic_wav


@pytest.fixture(autouse=True)
def setup_test_cache(tmp_path, monkeypatch):
    """Ensure test runs use a clean temporary cache directory."""
    test_cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(cache, "cache_dir", test_cache_dir)
    monkeypatch.setattr(cache, "enabled", True)
    test_cache_dir.mkdir(parents=True, exist_ok=True)
    yield


@pytest.mark.asyncio
async def test_mcp_server_registration_and_dispatch():
    """Verify that MCP server correctly registers all Indic tools and dispatcher executes them."""
    server = create_mcp_server()
    tools = await server.list_tools()
    tool_names = [t.name for t in tools]

    expected = [
        "bodhan_transcribe",
        "bodhan_translate",
        "bodhan_speak",
        "bodhan_ocr",
        "bodhan_inspect_media",
        "bodhan_chunk_audio",
    ]
    for exp in expected:
        assert exp in tool_names

    # Test direct in-process dispatch for translation
    trans_res = await dispatch_tool_call(
        "bodhan_translate",
        {"text": "Class starts at nine.", "source_lang": "en", "target_lang": "hi"},
    )
    assert "translated_text" in trans_res
    assert trans_res["target_language"] == "hi"
    assert trans_res["cost_inr"] > 0.0


@pytest.mark.asyncio
async def test_e2e_speech_to_speech_translation_pipeline(tmp_path):
    """E2E workflow: Transcribe Hindi audio -> Translate to Kannada -> Synthesize Kannada speech."""
    # 1. Create synthetic input audio (15 seconds)
    input_wav = create_synthetic_wav(tmp_path / "classroom_hindi.wav", duration_sec=15.0)

    out_dir = tmp_path / "run_speech_to_speech"
    runner = AgentRunner(
        task="Transcribe classroom_hindi.wav in Hindi, translate into Kannada, and synthesize speech in Kannada",
        files=[str(input_wav)],
        output_dir=out_dir,
        max_steps=10,
        max_cost_inr=5.0,
    )

    report = await runner.run()

    # Assertions
    assert report.status == ExecutionStatus.SUCCESS
    assert report.steps_taken == 3
    assert report.total_cost_inr > 0.0
    assert report.total_cost_inr <= 5.0
    assert "transcribe" in report.cost_breakdown
    assert "translate" in report.cost_breakdown
    assert "speak" in report.cost_breakdown

    # Verify generated artifacts
    assert len(report.artifacts) >= 3
    assert (out_dir / "result.md").exists()
    assert (out_dir / "report.json").exists()
    assert (out_dir / "trace.jsonl").exists()

    # Check generated audio has companion AI disclaimer
    audio_artifacts = [Path(a) for a in report.artifacts if a.endswith(".wav")]
    assert len(audio_artifacts) > 0
    generated_audio = audio_artifacts[0]
    meta_file = generated_audio.with_suffix(".json")
    assert meta_file.exists()
    with open(meta_file, "r", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta.get("ai_generated") is True
    assert meta.get("language") == "kn"


@pytest.mark.asyncio
async def test_e2e_document_ocr_to_speech_pipeline(tmp_path):
    """E2E workflow: OCR document image -> Translate to Tamil -> Synthesize Tamil speech."""
    # Create synthetic test image
    img_path = tmp_path / "notice.png"
    img = Image.new("RGB", (400, 200), color=(255, 255, 255))
    img.save(img_path)

    out_dir = tmp_path / "run_ocr_to_speech"
    runner = AgentRunner(
        task="Extract text from notice.png using OCR, translate to Tamil, and synthesize speech in Tamil",
        files=[str(img_path)],
        output_dir=out_dir,
        max_steps=10,
        max_cost_inr=5.0,
    )

    report = await runner.run()

    assert report.status == ExecutionStatus.SUCCESS
    assert report.steps_taken == 3
    assert "ocr" in report.cost_breakdown
    assert "translate" in report.cost_breakdown
    assert "speak" in report.cost_breakdown

    # Ensure result.md has structured sections
    result_md = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "Bodhan Task Execution Report" in result_md
    assert "SUCCESS" in result_md
    assert "Incurred Cost Breakdown" in result_md


@pytest.mark.asyncio
async def test_e2e_caching_eliminates_duplicate_spend(tmp_path):
    """Re-executing an identical task must hit cache and record zero additional spend."""
    input_wav = create_synthetic_wav(tmp_path / "lecture.wav", duration_sec=5.0)

    out1 = tmp_path / "run_cache_1"
    runner1 = AgentRunner(
        task="Transcribe lecture.wav in Hindi and translate to Telugu",
        files=[str(input_wav)],
        output_dir=out1,
    )
    report1 = await runner1.run()
    cost_run1 = report1.total_cost_inr
    assert cost_run1 > 0.0

    # Second run with same input and task
    out2 = tmp_path / "run_cache_2"
    runner2 = AgentRunner(
        task="Transcribe lecture.wav in Hindi and translate to Telugu",
        files=[str(input_wav)],
        output_dir=out2,
    )
    report2 = await runner2.run()

    # Second run should have cost ₹0.0 due to content-addressed cache hit!
    assert report2.status == ExecutionStatus.SUCCESS
    assert report2.total_cost_inr == 0.0
