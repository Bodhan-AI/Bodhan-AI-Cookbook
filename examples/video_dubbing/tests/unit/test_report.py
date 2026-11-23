"""
Unit tests for Report generation and quality SLA targets.
"""

from pathlib import Path

import pytest

from bodhan_dub.gateway.cost import CostLedger
from bodhan_dub.stages.report import LanguageSummary, build_pipeline_report


@pytest.mark.asyncio
async def test_build_pipeline_report(tmp_path: Path):
    ledger = CostLedger()
    await ledger.record_transcribe(120.0)
    await ledger.record_translate(20000)
    await ledger.record_speak(2000)

    lang_hi = LanguageSummary(
        status="ok",
        total_segments=10,
        placed_segments=10,
        degraded_segments=0,
        skipped_segments=0,
        stretch_stats={"min": 0.95, "max": 1.15, "mean": 1.02},
        sync_offset_stats={"max_ms": 120.0, "p95_ms": 90.0},
        cer_stats={"mean": 0.045, "p95": 0.08},
        integrated_lufs=-16.1,
        duration_delta_ms=25.0,
        retries_count=1,
    )

    report = build_pipeline_report(
        run_id="test_run_123",
        input_file="/path/to/video.mp4",
        video_duration_s=120.0,
        lang_summaries={"hi": lang_hi},
        cost_ledger=ledger,
        stage_timings={"total_pipeline": 45.0},
    )

    assert report.run_id == "test_run_123"
    assert report.overall_status == "ok"
    assert report.ai_generated is True
    assert "AI-dubbed" in report.ai_label
    assert report.costs["total_cost_inr"] > 0

    # Verify target SLAs
    target_names = [t.name for t in report.targets]
    assert "Sync precision" in target_names
    assert "Time-stretch bounds" in target_names
    assert "Output stream sync" in target_names
    assert "EBU R128 loudness" in target_names

    for t in report.targets:
        assert t.met is True, f"Target {t.name} failed: {t.reason}"

    # Test JSON serialization
    report_file = tmp_path / "report.json"
    report.save(report_file)
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "test_run_123" in content
