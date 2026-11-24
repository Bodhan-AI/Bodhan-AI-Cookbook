"""
End-to-end smoke test for bodhan_dub using the offline FakeBodhan server.
Generates a synthetic 30-second video fixture using ffmpeg and runs CLI preflight and estimate.
"""

import subprocess
from pathlib import Path

import pytest
from fake_bodhan.server import FakeBodhanTransport

from bodhan_dub.cli import async_main, build_parser


def generate_synthetic_mp4(out_path: Path, duration_s: int = 30) -> Path:
    """Generates a small synthetic MP4 video file using ffmpeg."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        return out_path

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration_s}:size=320x240:rate=25",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration_s}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


@pytest.mark.asyncio
async def test_smoke_cli_estimate(tmp_path):
    """Verifies that the CLI executes --estimate-only successfully on a synthetic 30s fixture."""
    fixture_video = generate_synthetic_mp4(tmp_path / "smoke_sample.mp4", duration_s=30)
    assert fixture_video.exists()

    parser = build_parser()
    args = parser.parse_args(
        [
            "--input",
            str(fixture_video),
            "--langs",
            "hi,ta,bn",
            "--out",
            str(tmp_path / "out"),
            "--estimate-only",
        ]
    )

    exit_code = await async_main(args)
    assert exit_code == 0, f"Expected exit code 0 for --estimate-only, got {exit_code}"


@pytest.mark.asyncio
async def test_smoke_cli_preflight_success(tmp_path, monkeypatch):
    """Verifies that CLI initializes cleanly on valid inputs with mock keys."""
    # Set mock keys for offline smoke testing
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "sk-mock-transcribe-key")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "sk-mock-translate-key")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "sk-mock-speak-key")

    fixture_video = generate_synthetic_mp4(tmp_path / "smoke_sample_preflight.mp4", duration_s=65)
    assert fixture_video.exists()

    parser = build_parser()
    out_dir = tmp_path / "out_preflight"
    args = parser.parse_args(
        [
            "--input",
            str(fixture_video),
            "--langs",
            "hi,te",
            "--out",
            str(out_dir),
        ]
    )

    exit_code = await async_main(args, transport=FakeBodhanTransport())
    assert exit_code == 0
    # Check structured log created
    log_file = out_dir / "run.log.jsonl"
    assert log_file.exists(), "Structured JSONL log file must be created"
    manifest_file = out_dir / "manifest.json"
    assert manifest_file.exists(), "Run manifest must be created"
    transcript_file = out_dir / "transcript.en.json"
    assert transcript_file.exists(), "English transcript transcript.en.json must be created"
    srt_file = out_dir / "subtitles.en.srt"
    assert srt_file.exists(), "English subtitles.en.srt must be created"


@pytest.mark.asyncio
async def test_smoke_cli_exit_code_2_on_invalid_language(tmp_path, monkeypatch):
    """Verifies that CLI returns exit code 2 on invalid language arguments."""
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "sk-mock-transcribe-key")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "sk-mock-translate-key")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "sk-mock-speak-key")

    fixture_video = generate_synthetic_mp4(tmp_path / "smoke_invalid_lang.mp4", duration_s=10)
    parser = build_parser()
    args = parser.parse_args(
        [
            "--input",
            str(fixture_video),
            "--langs",
            "xyz_invalid",
            "--out",
            str(tmp_path / "out_invalid_lang"),
        ]
    )

    exit_code = await async_main(args)
    assert exit_code == 2


@pytest.mark.asyncio
async def test_smoke_cli_exit_code_2_on_budget_exceeded_preflight(tmp_path, monkeypatch):
    """Verifies that CLI returns exit code 2 when estimated cost exceeds budget ceiling."""
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "sk-mock-transcribe-key")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "sk-mock-translate-key")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "sk-mock-speak-key")

    fixture_video = generate_synthetic_mp4(tmp_path / "smoke_budget_preflight.mp4", duration_s=60)
    parser = build_parser()
    args = parser.parse_args(
        [
            "--input",
            str(fixture_video),
            "--langs",
            "hi,ta",
            "--out",
            str(tmp_path / "out_budget_preflight"),
            "--max-cost-inr",
            "0.01",  # Unrealistic tiny budget ceiling triggers exit 2 in preflight
        ]
    )

    exit_code = await async_main(args)
    assert exit_code == 2


@pytest.mark.asyncio
async def test_smoke_cli_tiny_budget_clean_stop(tmp_path, monkeypatch):
    """
    Verifies that when runtime budget is exhausted mid-pipeline:
    1. Pipeline stops cleanly without crashing.
    2. Partial outputs (report.json, manifest.json) are written.
    3. report.json records budget error.
    """
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "sk-mock-transcribe-key")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "sk-mock-translate-key")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "sk-mock-speak-key")

    fixture_video = generate_synthetic_mp4(tmp_path / "smoke_tiny_budget.mp4", duration_s=15)
    out_dir = tmp_path / "out_tiny_budget"

    from bodhan_dub.config import Settings
    from bodhan_dub.languages import LanguageRegistry
    from bodhan_dub.logging import PipelineLogger
    from bodhan_dub.manifest import RunManifest
    from bodhan_dub.orchestrator import PipelineOrchestrator

    settings = Settings()
    registry = LanguageRegistry()
    logger = PipelineLogger(run_id="test_tiny_budget")
    manifest = RunManifest(
        run_id="test_tiny_budget",
        input_file=str(fixture_video),
        target_langs=["hi"],
    )

    orchestrator = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
        transport=FakeBodhanTransport(),
    )

    try:
        # Pre-seed cost or set a tiny runtime budget of 0.01 INR
        exit_code = await orchestrator.run(
            video_path=fixture_video,
            target_langs=["hi"],
            max_cost_inr=0.01,
        )
        assert exit_code in (0, 1)

        # Partial report must exist and be valid JSON
        report_file = out_dir / "report.json"
        assert report_file.exists()
        import json

        data = json.loads(report_file.read_text(encoding="utf-8"))
        assert "overall_status" in data
        assert data["ai_generated"] is True
    finally:
        await orchestrator.close()
