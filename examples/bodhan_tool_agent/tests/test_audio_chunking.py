"""Tests for audio chunking, pause detection, probing, and hard caps (Slice 4)."""

import math
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from bodhan_mcp.utils import audio


@pytest.fixture
def multi_minute_wav(tmp_path: Path) -> Path:
    """Generate a 70-second synthetic WAV audio with deliberate pauses at 24s and 48s."""
    sample_rate = 16000
    duration_s = 70.0
    num_samples = int(sample_rate * duration_s)

    time = np.linspace(0, duration_s, num_samples, endpoint=False)
    # Sine wave signal
    signal = 0.5 * np.sin(2 * math.pi * 440 * time)

    # Insert 500ms silences at 24.0s - 24.5s and 48.0s - 48.5s
    pause1_start = int(24.0 * sample_rate)
    pause1_end = int(24.5 * sample_rate)
    signal[pause1_start:pause1_end] = 0.0

    pause2_start = int(48.0 * sample_rate)
    pause2_end = int(48.5 * sample_rate)
    signal[pause2_start:pause2_end] = 0.0

    wav_file = tmp_path / "long_audio.wav"
    sf.write(str(wav_file), signal.astype("float32"), sample_rate)
    return wav_file


def test_probe_valid_and_missing_audio(short_wav: Path, tmp_path: Path):
    """Verify probe returns metadata and raises FileNotFoundError on missing files."""
    meta = audio.probe(short_wav)
    assert meta["duration_s"] == pytest.approx(1.0, 0.05)
    assert meta["sample_rate"] == 16000
    assert meta["channels"] == 1
    assert "wav" in meta["format"]

    with pytest.raises(FileNotFoundError):
        audio.probe(tmp_path / "non_existent.wav")


def test_find_pauses(multi_minute_wav: Path):
    """Verify silence detection correctly identifies the inserted pause intervals."""
    pauses = audio.find_pauses(multi_minute_wav, min_silence_ms=250)
    assert len(pauses) >= 2
    # Pause 1 should be around 24.25s
    assert any(23.8 <= p <= 24.7 for p in pauses)
    # Pause 2 should be around 48.25s
    assert any(47.8 <= p <= 48.7 for p in pauses)


def test_chunk_at_pauses_respects_limits_and_no_loss(multi_minute_wav: Path, tmp_path: Path):
    """Verify audio chunking cuts at pauses, enforces <=30s hard limit, and loses zero audio."""
    out_dir = tmp_path / "chunks"
    chunks = audio.chunk_at_pauses(multi_minute_wav, out_dir=out_dir, target_s=25.0, hard_s=30.0)

    # 70s should be split into 3 chunks
    assert len(chunks) == 3

    total_chunk_duration = 0.0
    total_chunk_samples = 0

    for idx, c in enumerate(chunks):
        chunk_path = Path(c.path)
        assert chunk_path.exists()
        chunk_data, sr = sf.read(str(chunk_path))
        chunk_duration = len(chunk_data) / sr

        # Hard limit invariant: each chunk MUST be <= 30.0s
        assert chunk_duration <= 30.0
        assert (c.end_s - c.start_s) == pytest.approx(chunk_duration, 0.01)

        total_chunk_duration += chunk_duration
        total_chunk_samples += len(chunk_data)

        # Check monotonic timestamps
        if idx > 0:
            assert c.start_s == pytest.approx(chunks[idx - 1].end_s, 0.01)

    # Verify first chunk cut at first pause (~24.25s)
    assert 23.5 <= chunks[0].end_s <= 25.0

    # No data loss invariant: total samples in chunks must equal original samples
    orig_data, _ = sf.read(str(multi_minute_wav))
    assert total_chunk_samples == len(orig_data)
    assert total_chunk_duration == pytest.approx(70.0, 0.05)


def test_short_audio_chunking_returns_single_chunk(short_wav: Path, tmp_path: Path):
    """Verify audio shorter than hard limit (1.0s <= 30s) produces a single chunk."""
    chunks = audio.chunk_at_pauses(short_wav, out_dir=tmp_path / "single_chunk")
    assert len(chunks) == 1
    assert chunks[0].start_s == 0.0
    assert chunks[0].end_s == pytest.approx(1.0, 0.05)
