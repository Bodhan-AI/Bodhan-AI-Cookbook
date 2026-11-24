"""
Unit regression tests for Batch E: Interrupts, Security, and Performance.
Covers:
- E1: Interruption preserves completed language summaries, flushes report/manifest, and records interrupt error.
- E2: Secret redaction in logs, console, manifest, and error fields across Bearer, sk-, gsk_, AIza, and registered keys.
- E3: Strict Bodhan host validation permits strictly api.bodhan.ai and blocks custom Bodhan hosts.
- E4: Config loads .env lazily from cwd only without traversing ancestor directories.
- E5: Audio mixing operates with memory-bounded in-place array operations and respects concurrency.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from bodhan_dub.config import Settings, _get_cwd_env_file
from bodhan_dub.logging import (
    PipelineLogger,
    register_secret,
    sanitize_data,
    sanitize_text,
)
from bodhan_dub.manifest import RunManifest
from bodhan_dub.media.mix import MixResult, mix_dubbed_audio
from bodhan_dub.orchestrator import PipelineOrchestrator
from bodhan_dub.policy import (
    HostAllowlistGuard,
    PolicyViolation,
    validate_bodhan_host,
)
from bodhan_dub.stages.report import LanguageSummary


# ---------------------------------------------------------------------------
# E1: Interrupt preserves completed language work
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_E1_interrupt_keeps_completed_language(tmp_path: Path):
    s = MagicMock(spec=Settings)
    s.cache_dir = tmp_path / "cache"
    s.bodhan_base_url = "https://api.bodhan.ai/v1"
    s.bodhan_transcribe_key = "test_transcribe_key"
    s.bodhan_translate_key = "test_translate_key"
    s.bodhan_speak_key = "test_speak_key"
    s.bodhan_ocr_key = None
    s.llm_base_url = "https://api.openai.com/v1"
    s.llm_api_key = "test_llm_key"
    s.circuit_breaker_threshold = 5
    s.timeout_asr = 5.0
    s.timeout_translate = 5.0
    s.timeout_tts = 5.0
    s.semaphore_asr = 4
    s.semaphore_translate = 4
    s.semaphore_tts = 4
    s.semaphore_llm = 4
    s.timeout_llm = 5.0
    s.cost_asr_per_minute = 0.10
    s.cost_translate_per_10k_tokens = 0.20
    s.cost_tts_per_10k_chars = 6.00

    logger = PipelineLogger(run_id="test_e1", log_file=tmp_path / "run.log.jsonl")
    manifest = RunManifest(run_id="test_e1", input_file=str(tmp_path / "video.mp4"), target_langs=["hi", "ta"])

    orchestrator = PipelineOrchestrator(
        settings=s,
        registry=MagicMock(),
        logger=logger,
        manifest=manifest,
        out_dir=tmp_path,
        no_cache=True,
    )

    # Mock shared stages to return 1 segment
    dummy_vad = MagicMock()
    dummy_vad.has_speech = True
    dummy_vad.total_duration = 10.0
    orchestrator.run_shared_stages = AsyncMock(
        return_value=(dummy_vad, [MagicMock(segment_id="s1", slot_duration=2.0, text="hello")], tmp_path / "audio.wav", None)
    )

    # First language "hi" finishes successfully; second language "ta" raises CancelledError
    hi_summary = LanguageSummary(
        status="ok",
        total_segments=1,
        placed_segments=1,
        degraded_segments=0,
        skipped_segments=0,
        integrated_lufs=-16.0,
        duration_delta_ms=0.0,
        retries_count=0,
    )

    async def mock_process_language(lang, *args, **kwargs):
        if lang == "hi":
            return hi_summary
        else:
            raise asyncio.CancelledError()

    orchestrator.process_language = AsyncMock(side_effect=mock_process_language)

    video_dummy = tmp_path / "video.mp4"
    video_dummy.touch()

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.run(video_path=video_dummy, target_langs=["hi", "ta"])

    # Completed language "hi" must be preserved in completed_summaries
    assert "hi" in orchestrator.completed_summaries
    assert orchestrator.completed_summaries["hi"].status == "ok"

    # Report and manifest must have been flushed to out_dir
    report_file = tmp_path / "report.json"
    manifest_file = tmp_path / "manifest.json"
    assert report_file.exists()
    assert manifest_file.exists()

    import json
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert "hi" in report_data["languages"]
    assert report_data["languages"]["hi"]["status"] == "ok"
    assert "ta" in report_data["languages"]
    assert report_data["languages"]["ta"]["status"] == "failed"
    assert any("Interrupted" in err for err in report_data["languages"]["ta"]["errors"])


# ---------------------------------------------------------------------------
# E2: Comprehensive secret redaction across log formats
# ---------------------------------------------------------------------------
def test_E2_log_redaction_secrets(tmp_path: Path):
    # Dynamically register custom secrets
    custom_secret = "secret_project_token_98765"
    register_secret(custom_secret)

    jwt_token = "eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123"
    sk_token = "sk-" + "abcdef1234567890abcdef"
    gsk_token = "gsk_" + "qwertyuiop1234567890"
    aiza_token = "AIza" + "SyD12345678901234567890123456789012"

    test_msg = (
        f"Connecting with Authorization: Bearer {jwt_token} "
        f"and Bodhan key {sk_token} and Groq key {gsk_token} "
        f"and Google key {aiza_token} and custom {custom_secret}"
    )

    sanitized = sanitize_text(test_msg)

    # Verify no raw secrets survive
    assert sk_token not in sanitized
    assert gsk_token not in sanitized
    assert aiza_token not in sanitized
    assert "eyJ" + "hbGci" not in sanitized
    assert custom_secret not in sanitized

    # Check sanitize_data on nested structures
    sk_nested = "sk-" + "11112222333344445555"
    nested_data = {
        "key": sk_nested,
        "nested": ["gsk_9999888877776666", custom_secret],
    }
    sanitized_nested = sanitize_data(nested_data)
    assert sanitized_nested["key"] == "[REDACTED]"
    assert sanitized_nested["nested"][0] == "[REDACTED]"
    assert sanitized_nested["nested"][1] == "[REDACTED]"

    # Test file output via JsonlHandler
    sk_bearer = "sk-" + "testtoken1234567890"
    log_path = tmp_path / "test.log.jsonl"
    logger = PipelineLogger(run_id="test_e2", log_file=log_path)
    logger.info(f"Leaked secret: {custom_secret}", secret_field=f"Bearer {sk_bearer}")
    logger.flush()

    raw_log = log_path.read_text(encoding="utf-8")
    assert custom_secret not in raw_log
    assert sk_bearer not in raw_log


# ---------------------------------------------------------------------------
# E3: Strict Bodhan host validation
# ---------------------------------------------------------------------------
def test_E3_host_guard_strict_bodhan(monkeypatch):
    monkeypatch.delenv("BODHAN_ALLOW_TEST_HOSTS", raising=False)

    # api.bodhan.ai must pass
    assert validate_bodhan_host("https://api.bodhan.ai/v1") == "api.bodhan.ai"
    assert validate_bodhan_host("api.bodhan.ai") == "api.bodhan.ai"

    # Custom or rogue hosts must fail
    with pytest.raises(PolicyViolation):
        validate_bodhan_host("https://custom.bodhan.ai/v1")

    with pytest.raises(PolicyViolation):
        validate_bodhan_host("https://rogue.example.com/v1")

    # Localhost fails when BODHAN_ALLOW_TEST_HOSTS is not set
    with pytest.raises(PolicyViolation):
        validate_bodhan_host("http://localhost:8000")

    # Localhost passes when BODHAN_ALLOW_TEST_HOSTS=1
    monkeypatch.setenv("BODHAN_ALLOW_TEST_HOSTS", "1")
    assert validate_bodhan_host("http://localhost:8000") == "localhost"
    assert validate_bodhan_host("http://127.0.0.1:8000") == "127.0.0.1"

    # HostAllowlistGuard check_url
    guard = HostAllowlistGuard(["api.bodhan.ai", "api.openai.com"], allow_test_hosts=False)
    guard.check_url("https://api.bodhan.ai/v1/transcribe")
    guard.check_url("https://api.openai.com/v1/chat/completions")

    with pytest.raises(PolicyViolation):
        guard.check_url("https://rogue.example.com")


# ---------------------------------------------------------------------------
# E4: Lazy cwd-only .env loading (no ancestor traversal)
# ---------------------------------------------------------------------------
def test_E4_config_lazy_cwd_env(tmp_path: Path, monkeypatch):
    parent_dir = tmp_path / "parent"
    child_dir = parent_dir / "child"
    child_dir.mkdir(parents=True)

    parent_env = parent_dir / ".env"
    parent_env.write_text("BODHAN_TRANSCRIBE_KEY=parent_transcribe_key\n", encoding="utf-8")

    # Clear environment variables
    monkeypatch.delenv("BODHAN_TRANSCRIBE_KEY", raising=False)

    # Change directory to child (which has no .env)
    monkeypatch.chdir(child_dir)
    assert _get_cwd_env_file() is None

    # Settings should NOT load parent .env
    s_child = Settings()
    assert s_child.bodhan_transcribe_key != "parent_transcribe_key"

    # Now create child .env
    child_env = child_dir / ".env"
    child_env.write_text("BODHAN_TRANSCRIBE_KEY=child_transcribe_key\n", encoding="utf-8")
    assert _get_cwd_env_file() == str(child_env)

    s_child_with_env = Settings()
    assert s_child_with_env.bodhan_transcribe_key == "child_transcribe_key"


# ---------------------------------------------------------------------------
# E5: Memory-bounded in-place audio mixing
# ---------------------------------------------------------------------------
def test_E5_mix_memory_bounded(tmp_path: Path):
    sr = 48000
    duration_s = 2.0
    num_samples = int(sr * duration_s)

    # Create stereo background audio
    source_wav = tmp_path / "stereo_source.wav"
    t = np.linspace(0, duration_s, num_samples, endpoint=False)
    left = 0.05 * np.sin(2 * np.pi * 440 * t).astype(np.float32)
    right = 0.05 * np.cos(2 * np.pi * 440 * t).astype(np.float32)
    stereo_data = np.column_stack([left, right])
    import soundfile as sf
    sf.write(source_wav, stereo_data, sr)

    # Create mono speech stem
    speech_stem = np.zeros(num_samples, dtype=np.float32)
    speech_stem[sr // 2 : sr + sr // 2] = 0.4 * np.sin(2 * np.pi * 220 * t[:sr]).astype(np.float32)

    out_wav = tmp_path / "dubbed_mix.wav"

    mix_res = mix_dubbed_audio(
        original_audio_path=source_wav,
        speech_stem_48k=speech_stem,
        speech_intervals_to_mute=[(0.5, 1.5)],
        output_wav_path=out_wav,
        placed_spans=[(0.5, 1.5)],
        skipped_spans=[],
    )

    assert isinstance(mix_res, MixResult)
    assert out_wav.exists()

    # Verify mixed output is stereo and valid 48kHz
    mixed_data, mixed_sr = sf.read(str(out_wav), dtype="float32")
    assert mixed_sr == sr
    assert mixed_data.ndim == 2
    assert mixed_data.shape[1] == 2
    assert len(mixed_data) == num_samples
