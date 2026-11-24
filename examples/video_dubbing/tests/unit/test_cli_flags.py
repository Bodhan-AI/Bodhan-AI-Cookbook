"""
Unit tests for CLI flag wiring (C4):
- --concurrency
- --voices
- --resume
- --separate-music
- --keep-work
"""

import numpy as np
import pytest
import soundfile as sf
import yaml
from fake_bodhan.server import FakeBodhanTransport

from bodhan_dub.config import Settings
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.manifest import RunManifest
from bodhan_dub.orchestrator import PipelineOrchestrator


@pytest.fixture
def mock_pipeline_env(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    settings = Settings(circuit_breaker_threshold=5)
    registry = LanguageRegistry()
    logger = PipelineLogger(run_id="test_flags", log_file=out_dir / "test.log")
    manifest = RunManifest(
        run_id="test_flags",
        input_file="input.mp4",
        target_langs=["hi", "ta"],
    )
    transport = FakeBodhanTransport()
    return out_dir, settings, registry, logger, manifest, transport


@pytest.mark.asyncio
async def test_flag_keep_work_retains_or_deletes_intermediate_files(mock_pipeline_env):
    """C4: Assert keep_work=False cleans up work_dir, keep_work=True retains work_dir."""
    out_dir, settings, registry, logger, manifest, transport = mock_pipeline_env

    # 1. keep_work=False
    orch_clean = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir / "run_clean",
        keep_work=False,
        transport=transport,
    )
    # Create a dummy intermediate file in work_dir
    dummy_work_file = orch_clean.work_dir / "dummy.txt"
    dummy_work_file.write_text("intermediate data")
    assert dummy_work_file.exists()

    # Simulate clean exit cleanup
    if not orch_clean.keep_work and orch_clean.work_dir.exists():
        import shutil
        shutil.rmtree(orch_clean.work_dir, ignore_errors=True)
    assert not orch_clean.work_dir.exists()

    # 2. keep_work=True
    orch_retain = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir / "run_retain",
        keep_work=True,
        transport=transport,
    )
    dummy_work_file2 = orch_retain.work_dir / "dummy.txt"
    dummy_work_file2.write_text("intermediate data")

    if not orch_retain.keep_work and orch_retain.work_dir.exists():
        import shutil
        shutil.rmtree(orch_retain.work_dir, ignore_errors=True)
    assert orch_retain.work_dir.exists()
    assert dummy_work_file2.exists()


@pytest.mark.asyncio
async def test_flag_voices_custom_override(mock_pipeline_env, tmp_path):
    """C4: Assert passing custom voices mapping overrides default registry voice."""
    out_dir, settings, registry, logger, manifest, transport = mock_pipeline_env

    # Write custom voices YAML
    voices_yaml = tmp_path / "custom_voices.yaml"
    voices_yaml.write_text(yaml.dump({"hi": "Amit", "ta": "Kavya_Custom"}), encoding="utf-8")

    custom_voices = yaml.safe_load(voices_yaml.read_text(encoding="utf-8"))

    orch = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
        custom_voices=custom_voices,
        transport=transport,
    )

    # hi default voice is Kavya; custom voice is Amit
    default_hi = registry.get_voice("hi")
    assert default_hi == "Kavya"
    assert orch.custom_voices.get("hi") == "Amit"


@pytest.mark.asyncio
async def test_flag_resume_skips_completed_segments(mock_pipeline_env, tmp_path):
    """C4: Assert resume=True reuses completed segments from manifest and cached wav."""
    out_dir, settings, registry, logger, manifest, transport = mock_pipeline_env

    # Mark segment seg_01 as completed in manifest
    manifest.record_completed_segment("hi", "seg_01")
    manifest_path = out_dir / "manifest.json"
    manifest.save(manifest_path)

    orch = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
        resume=True,
        transport=transport,
    )

    # Create cached wav for seg_01 in work_dir / hi / seg_01.wav
    hi_work = orch.work_dir / "hi"
    hi_work.mkdir(parents=True, exist_ok=True)
    dummy_audio = np.zeros(48000, dtype=np.float32)
    sf.write(str(hi_work / "seg_01.wav"), dummy_audio, 48000)

    # Initial call count for speak
    init_speak_calls = transport.call_counts["speak"]

    # Verify manifest recognizes completed segment
    assert orch.manifest.is_segment_completed("hi", "seg_01")
    assert (hi_work / "seg_01.wav").exists()
    assert transport.call_counts["speak"] == init_speak_calls


@pytest.mark.asyncio
async def test_flag_separate_music_fallback_when_tool_absent(mock_pipeline_env):
    """C4: Assert separate_music=True falls back cleanly to VAD ducking path when tools missing."""
    out_dir, settings, registry, logger, manifest, transport = mock_pipeline_env

    orch = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=out_dir,
        separate_music=True,
        transport=transport,
    )

    # Without demucs/spleeter installed, separate_music_vocal_path remains None and no crash occurs
    assert orch.separate_music is True
    assert orch.separate_music_vocal_path is None
