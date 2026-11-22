"""Unit tests for audio chunking logic."""
from __future__ import annotations

import struct
import wave
from pathlib import Path

from pydub import AudioSegment

from bodhan_mcp.tools.audio_chunk import chunk_audio


def _make_wav(path: Path, duration_s: float, sample_rate: int = 16000) -> Path:
    """Write a silent WAV file of the given duration."""
    n_samples = int(duration_s * sample_rate)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return path


def test_short_audio_returns_original(tmp_path: Path) -> None:
    """Files under max_seconds are returned as-is."""
    wav = _make_wav(tmp_path / "short.wav", duration_s=10.0)
    chunks = chunk_audio(wav, max_seconds=28.0)
    assert chunks == [wav]
    assert len(chunks) == 1


def test_long_audio_produces_multiple_chunks(tmp_path: Path) -> None:
    """Files longer than max_seconds are split into at least 2 chunks."""
    # Create a 60-second silent file (pydub will find no silence to split on,
    # so the fallback hard-split at midpoint should fire)
    wav = _make_wav(tmp_path / "long.wav", duration_s=60.0)
    chunks = chunk_audio(wav, max_seconds=28.0)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.exists()
        seg = AudioSegment.from_file(str(chunk))
        # Each chunk should be at most max_seconds + small overhead
        assert len(seg) <= 29_000  # ms


def test_exact_boundary_audio_returns_original(tmp_path: Path) -> None:
    """File exactly at the limit returns [original]."""
    wav = _make_wav(tmp_path / "exact.wav", duration_s=28.0)
    chunks = chunk_audio(wav, max_seconds=28.0)
    assert chunks == [wav]


def test_chunks_are_wav_files(tmp_path: Path) -> None:
    """All produced chunk files are readable WAV files."""
    wav = _make_wav(tmp_path / "audio.wav", duration_s=70.0)
    chunks = chunk_audio(wav, max_seconds=28.0)
    for chunk in chunks:
        seg = AudioSegment.from_file(str(chunk))
        assert len(seg) > 0
