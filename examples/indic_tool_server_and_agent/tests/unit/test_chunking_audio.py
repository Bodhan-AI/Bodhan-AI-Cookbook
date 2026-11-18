"""Unit tests for audio chunking (Indic-Transcribe 30s window boundary)."""
from __future__ import annotations

import os
from pathlib import Path
import pytest

from bodhan_mcp.core.chunking import (
    split_audio_chunks,
    chunk_audio,
    get_audio_duration_seconds,
)
from bodhan_mcp.core.errors import BodhanError
from tests.conftest import make_wav


@pytest.mark.parametrize(
    "duration_s,expected_chunks",
    [
        (1.0, 1),
        (29.9, 1),
        (30.0, 1),
        (30.1, 2),
        (60.0, 2),
        (61.0, 3),
        (180.0, 6),
    ],
)
def test_audio_chunk_count_and_boundaries(duration_s: float, expected_chunks: int, tmp_path: Path):
    """Test chunk counts and max duration bounds across boundary values."""
    wav_data = make_wav(duration_s)
    audio_path = tmp_path / f"audio_{duration_s}s.wav"
    audio_path.write_bytes(wav_data)

    chunks = split_audio_chunks(str(audio_path), max_seconds=30)
    assert len(chunks) == expected_chunks

    for chunk in chunks:
        # Check attribute and indexing access
        assert chunk.duration_seconds <= 30.05  # small float tolerance
        assert os.path.exists(chunk.path) or os.path.exists(chunk[2])


def test_audio_zero_length(tmp_path: Path):
    """0s audio returns 0 chunks or raises clean error."""
    audio_path = tmp_path / "zero.wav"
    audio_path.write_bytes(make_wav(0.0))

    try:
        chunks = split_audio_chunks(str(audio_path), max_seconds=30)
        assert len(chunks) == 0
    except BodhanError:
        # Raising a clean BodhanError is also acceptable
        pass


def test_audio_corrupt_file(tmp_path: Path):
    """Corrupt audio file raises clean BodhanError without crashing."""
    corrupt_file = tmp_path / "corrupt.wav"
    corrupt_file.write_bytes(b"not a real audio file content at all")

    with pytest.raises(BodhanError):
        chunk_audio(str(corrupt_file), chunk_seconds=30)


def test_audio_different_sample_rates(tmp_path: Path):
    """Test chunking with different sample rates (8000Hz, 44100Hz)."""
    for sr in (8000, 44100):
        wav_data = make_wav(35.0, sample_rate=sr)
        p = tmp_path / f"sr_{sr}.wav"
        p.write_bytes(wav_data)

        chunks = split_audio_chunks(str(p), max_seconds=30)
        assert len(chunks) == 2
        for c in chunks:
            assert c.duration_seconds <= 30.05


def test_audio_total_coverage_no_gaps(tmp_path: Path):
    """Test that split chunks cover the entire audio with no time gaps."""
    duration = 75.0
    wav_data = make_wav(duration)
    p = tmp_path / "coverage.wav"
    p.write_bytes(wav_data)

    chunks = split_audio_chunks(str(p), max_seconds=30)
    assert len(chunks) == 3

    # Check contiguous coverage: start_ms of chunk N should match end_ms of chunk N-1
    for i in range(len(chunks) - 1):
        assert abs(chunks[i].end_ms - chunks[i + 1].start_ms) < 1.0

    # Total duration covered should match original file
    assert abs(chunks[-1].end_seconds - duration) < 0.2
