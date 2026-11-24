"""Unit tests for audio chunking at natural pauses and SRT formatting."""

import os
import tempfile
import numpy as np
import soundfile as sf
import pytest
from bodhan_mcp.audio_helpers import (
    format_srt_timestamp,
    split_audio_at_pauses,
    build_srt_content,
    split_text_into_cues,
)
from bodhan_mcp.types import SubtitleSegment


def test_format_srt_timestamp():
    assert format_srt_timestamp(0.0) == "00:00:00,000"
    assert format_srt_timestamp(1.234) == "00:00:01,234"
    assert format_srt_timestamp(65.5) == "00:01:05,500"
    assert format_srt_timestamp(3661.05) == "01:01:01,050"


def test_split_audio_at_pauses():
    """Create a 35-second synthetic audio with pauses and test pause cutting."""
    sr = 16000
    duration_s = 35.0
    total_samples = int(sr * duration_s)

    # Signal: speech bursts separated by silent pauses
    audio = np.zeros(total_samples, dtype=np.float32)

    # Burst 1: 0 to 10s (speech)
    t = np.linspace(0, 10, int(sr * 10), endpoint=False)
    audio[0:int(sr * 10)] = 0.5 * np.sin(2 * np.pi * 440 * t)

    # Pause 1: 10 to 12s (silence - energy is 0)

    # Burst 2: 12 to 22s (speech)
    t = np.linspace(0, 10, int(sr * 10), endpoint=False)
    audio[int(sr * 12):int(sr * 22)] = 0.5 * np.sin(2 * np.pi * 440 * t)

    # Pause 2: 22 to 24s (silence)

    # Burst 3: 24 to 35s (speech)
    t = np.linspace(0, 11, int(sr * 11), endpoint=False)
    audio[int(sr * 24):] = 0.5 * np.sin(2 * np.pi * 440 * t)

    temp_dir = tempfile.mkdtemp(prefix="test_audio_")
    test_wav = os.path.join(temp_dir, "test_35s.wav")
    sf.write(test_wav, audio, sr)

    chunks = split_audio_at_pauses(test_wav, output_dir=temp_dir, min_chunk_s=8.0, max_chunk_s=28.0)

    # Assertions
    assert len(chunks) >= 2, "Audio > 28s must be split into at least 2 chunks"
    for start_s, end_s, path in chunks:
        chunk_len = end_s - start_s
        assert chunk_len <= 28.0, f"Chunk duration ({chunk_len}s) must not exceed 28.0s limit"
        assert os.path.exists(path), f"Chunk file must exist: {path}"

    # First cut should occur near the first pause (10s - 12s)
    first_chunk_end = chunks[0][1]
    assert 9.0 <= first_chunk_end <= 13.0, f"First cut should occur at pause around 10-12s, got {first_chunk_end}s"


def test_build_srt_content():
    segments = [
        SubtitleSegment(index=1, start_time="00:00:00,000", end_time="00:00:08,500", start_seconds=0.0, end_seconds=8.5, text="Hello world"),
        SubtitleSegment(index=2, start_time="00:00:08,500", end_time="00:00:15,000", start_seconds=8.5, end_seconds=15.0, text="This is a test"),
    ]
    srt_text = build_srt_content(segments)
    assert "1\n00:00:00,000 --> 00:00:08,500\nHello world" in srt_text
    assert "2\n00:00:08,500 --> 00:00:15,000\nThis is a test" in srt_text


def test_split_text_into_cues_sentence_boundaries():
    """Verify sentence boundary splitting, proportional timing, and <= 42 chars/line."""
    sample_text = (
        "Hello everyone, today's important meeting will be held at 3 PM. "
        "Please join on time. We have a total of five main points to discuss. Thank you."
    )
    cues = split_text_into_cues(sample_text, start_seconds=0.0, end_seconds=13.483, max_chars_per_line=42, max_lines=2)

    assert len(cues) >= 4, f"Expected at least 4 sentence cues, got {len(cues)}"

    # Timings must be strictly monotonic
    for i in range(len(cues)):
        assert cues[i].start_seconds < cues[i].end_seconds
        if i > 0:
            assert cues[i].start_seconds >= cues[i - 1].end_seconds - 0.005

    # End matches chunk duration within 100ms
    assert abs(cues[-1].end_seconds - 13.483) < 0.1

    # Check formatting limits: max 2 lines, <= 42 chars per line
    for cue in cues:
        lines = cue.text.split("\n")
        assert len(lines) <= 2, f"Cue exceeds 2 lines: {cue.text}"
        for l in lines:
            assert len(l) <= 45, f"Line length exceeds 42 (+margin): {l}"


def test_transcribe_cost_accounting_partial_cache(tmp_path, monkeypatch):
    """Verify that transcribe_audio computes cost strictly for uncached chunks."""
    from bodhan_mcp.tools.transcribe import transcribe_audio

    # Mock audio info for a 30-second file split into two 15s chunks
    monkeypatch.setattr(
        "bodhan_mcp.tools.transcribe.get_audio_info",
        lambda p: {"duration": 30.0, "samplerate": 16000, "channels": 1, "format": "WAV"},
    )
    monkeypatch.setattr(
        "bodhan_mcp.tools.transcribe.split_audio_at_pauses",
        lambda p, **kw: [(0.0, 15.0, "c1.wav"), (15.0, 30.0, "c2.wav")],
    )

    # First chunk is cached (is_cached=True), second chunk is new (is_cached=False)
    responses = iter([("chunk 1 text", True), ("chunk 2 text", False)])
    monkeypatch.setattr(
        "bodhan_mcp.tools.transcribe.bodhan_client.transcribe",
        lambda path, **kw: next(responses),
    )

    dummy_audio = tmp_path / "test_partial.wav"
    dummy_audio.write_text("dummy")

    res = transcribe_audio(str(dummy_audio))

    # Cost for 15s uncached: 15 / 60 * 0.10 = 0.0250 INR
    assert res.cost_inr == 0.025
    assert not res.cached
    assert "chunk 1 text chunk 2 text" == res.text


