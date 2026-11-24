"""Tests for audio chunking logic in bodhan_core.audio."""
import math
import struct
import wave
import pathlib
import pytest


# ---------------------------------------------------------------------------
# Helper: synthetic sine-wave WAV generator
# ---------------------------------------------------------------------------

def make_sine_wav(path, duration_secs=5, freq=440, sample_rate=16000):
    """Generate a pure sine tone WAV for testing (fast batched write)."""
    path = pathlib.Path(path)
    # Generate 1 second of samples
    one_sec = bytearray()
    for i in range(sample_rate):
        v = int(32767 * math.sin(2 * math.pi * freq * i / sample_rate))
        one_sec.extend(struct.pack("<h", v))

    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Repeat 1-sec block
        full_secs = int(duration_secs)
        rem = duration_secs - full_secs
        wf.writeframes(bytes(one_sec) * full_secs)
        if rem > 0:
            rem_frames = int(rem * sample_rate) * 2
            wf.writeframes(bytes(one_sec[:rem_frames]))
    return path


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

try:
    from bodhan_core.audio import chunk_audio, convert_to_wav, probe_audio, stitch_audio
    _HAVE_AUDIO = True
except ImportError:
    _HAVE_AUDIO = False

pytestmark = pytest.mark.skipif(not _HAVE_AUDIO, reason="bodhan_core.audio not available")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_probe_audio_returns_correct_duration(tmp_path):
    """probe_audio should return the duration in seconds (±0.5 s tolerance)."""
    wav = make_sine_wav(tmp_path / "tone_5s.wav", duration_secs=5)
    duration = probe_audio(wav)
    assert abs(duration - 5.0) < 0.5, f"Expected ~5 s, got {duration}"


def test_chunk_audio_all_chunks_lte_30s(tmp_path):
    """Every chunk produced by chunk_audio must be ≤ 30 seconds."""
    wav = make_sine_wav(tmp_path / "long.wav", duration_secs=90)
    out_dir = tmp_path / "chunks"
    out_dir.mkdir()
    chunks = chunk_audio(wav, out_dir=out_dir, max_duration_secs=30)
    assert len(chunks) > 0
    for chunk in chunks:
        d = probe_audio(chunk)
        assert d <= 30.5, f"Chunk {chunk.name} is {d:.2f} s — exceeds 30 s"


def test_chunk_audio_short_file_returns_one_chunk(tmp_path):
    """A 10-second file chunked at 30 s should produce exactly 1 chunk."""
    wav = make_sine_wav(tmp_path / "short.wav", duration_secs=10)
    out_dir = tmp_path / "chunks_short"
    out_dir.mkdir()
    chunks = chunk_audio(wav, out_dir=out_dir, max_duration_secs=30)
    assert len(chunks) == 1


def test_convert_to_wav_produces_valid_wav(tmp_path):
    """convert_to_wav should produce a file readable by the wave module."""
    src = make_sine_wav(tmp_path / "source.wav", duration_secs=3)
    out = tmp_path / "converted.wav"
    result = convert_to_wav(src, out)
    # Must be a valid WAV
    with wave.open(str(result), "r") as wf:
        assert wf.getnframes() > 0
        assert wf.getnchannels() in (1, 2)
        assert wf.getsampwidth() > 0


def test_stitch_audio_longer_than_longest_chunk(tmp_path):
    """stitch_audio output must be longer than any individual input chunk."""
    chunks = []
    for i, dur in enumerate([5, 7, 6]):
        p = make_sine_wav(tmp_path / f"chunk_{i}.wav", duration_secs=dur)
        chunks.append(p)

    out = tmp_path / "stitched.wav"
    stitch_audio(chunks, out)

    result_dur = probe_audio(out)
    max_chunk_dur = max(probe_audio(c) for c in chunks)
    assert result_dur > max_chunk_dur, (
        f"Stitched duration {result_dur:.2f} s not longer than longest chunk {max_chunk_dur:.2f} s"
    )


def test_chunk_audio_exact_boundary(tmp_path):
    """A file exactly 30 seconds should produce exactly 1 chunk."""
    wav = make_sine_wav(tmp_path / "exact30.wav", duration_secs=30)
    out_dir = tmp_path / "chunks_exact"
    out_dir.mkdir()
    chunks = chunk_audio(wav, out_dir=out_dir, max_duration_secs=30)
    assert len(chunks) == 1


def test_chunk_audio_produces_correct_count(tmp_path):
    """A 65-second file chunked at 30 s should produce 3 chunks (30+30+5)."""
    wav = make_sine_wav(tmp_path / "sixtyfive.wav", duration_secs=65)
    out_dir = tmp_path / "chunks_65"
    out_dir.mkdir()
    chunks = chunk_audio(wav, out_dir=out_dir, max_duration_secs=30)
    # At 30 s max: ceil(65/30) = 3
    assert len(chunks) == 3
