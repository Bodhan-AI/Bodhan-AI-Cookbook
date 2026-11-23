"""
Unit tests for video/audio muxing and verification.
"""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.mux import MuxError, mux_and_verify, probe_media_streams
from tests.e2e.test_smoke import generate_synthetic_mp4


def test_mux_and_verify(tmp_path: Path):
    video_path = generate_synthetic_mp4(tmp_path / "source.mp4", duration_s=3)
    assert video_path.exists()

    # Generate 3 seconds of 48kHz audio WAV
    audio_path = tmp_path / "audio.wav"
    samples = np.zeros(48000 * 3, dtype=np.float32)
    sf.write(str(audio_path), samples, 48000)

    out_mp4 = tmp_path / "dubbed_video.mp4"
    logger = PipelineLogger(run_id="test_run")

    res = mux_and_verify(
        video_path=video_path,
        audio_path=audio_path,
        output_mp4_path=out_mp4,
        lang="hi",
        logger=logger,
    )

    assert res.verified is True
    assert out_mp4.exists()
    assert res.duration_delta_ms <= 100.0

    # Probe output to check metadata presence and clean flags without escaped quotes
    probe = probe_media_streams(out_mp4)
    format_tags = probe.get("format", {}).get("tags", {})
    assert "AI-dubbed with Bodhan Indic-Speak" in format_tags.get("comment", "")
    assert '"' not in format_tags.get("comment", "")  # No extra escaped quotes!

    # Check audio stream language tag
    audio_streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
    assert len(audio_streams) >= 1
    assert audio_streams[0].get("tags", {}).get("language") in ("hi", "hin")


def test_mux_preserves_duration_and_raises_on_severe_desync(tmp_path: Path):
    """B7: Output duration must strictly match input duration within ±100 ms or raise error."""
    video_path = generate_synthetic_mp4(tmp_path / "source_5s.mp4", duration_s=5)

    # 5 seconds audio
    audio_path = tmp_path / "audio_5s.wav"
    samples = np.zeros(48000 * 5, dtype=np.float32)
    sf.write(str(audio_path), samples, 48000)

    out_mp4 = tmp_path / "dubbed_5s.mp4"
    logger = PipelineLogger(run_id="test_run")

    res = mux_and_verify(
        video_path=video_path,
        audio_path=audio_path,
        output_mp4_path=out_mp4,
        lang="bn",
        logger=logger,
    )
    assert res.verified is True
    assert abs(res.video_duration_s - 5.0) < 0.1


def test_mux_no_audio(tmp_path: Path):
    """B3: Test mux fails when audio file is empty or missing."""
    video_path = generate_synthetic_mp4(tmp_path / "source_1s.mp4", duration_s=1)
    audio_path = tmp_path / "empty_audio.wav"
    audio_path.touch()

    out_mp4 = tmp_path / "out_no_audio.mp4"
    logger = PipelineLogger(run_id="test_run")
    with pytest.raises(MuxError):
        mux_and_verify(
            video_path=video_path,
            audio_path=audio_path,
            output_mp4_path=out_mp4,
            lang="hi",
            logger=logger,
        )
