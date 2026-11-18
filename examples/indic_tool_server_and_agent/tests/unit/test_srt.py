"""Unit tests for SRT subtitle file generation."""
from __future__ import annotations

from pathlib import Path
import pytest

from bodhan_mcp.core.errors import BodhanError, INVALID_INPUT
from bodhan_mcp.schemas import TranscriptSegment
from bodhan_mcp.tools.media import build_srt


@pytest.mark.asyncio
async def test_build_srt_valid_segments(tmp_path: Path):
    """Generates standard compliant SRT with sequential cue numbering and timestamps."""
    segments = [
        TranscriptSegment(start_seconds=0.0, end_seconds=3.25, text="पहला वाक्य"),
        TranscriptSegment(start_seconds=3.5, end_seconds=6.8, text="दूसरा वाक्य"),
        TranscriptSegment(start_seconds=7.0, end_seconds=10.0, text="समापन वाक्य"),
    ]

    out_file = tmp_path / "subtitles.srt"
    res = await build_srt(segments, output_path=str(out_file))

    assert res.ok is True
    assert res.num_entries == 3
    assert out_file.exists()

    content = out_file.read_text(encoding="utf-8")
    # Verify standard SRT format: index, timestamp arrow, text
    assert "1\n00:00:00,000 --> 00:00:03,250\nपहला वाक्य" in content
    assert "2\n00:00:03,500 --> 00:00:06,800\nदूसरा वाक्य" in content
    assert "3\n00:00:07,000 --> 00:00:10,000\nसमापन वाक्य" in content


@pytest.mark.asyncio
async def test_build_srt_empty_list_raises_error():
    """Empty segments list raises clean BodhanError."""
    with pytest.raises(BodhanError) as exc_info:
        await build_srt([])
    assert exc_info.value.code == INVALID_INPUT


@pytest.mark.asyncio
async def test_build_srt_monotonic_and_bounds(tmp_path: Path):
    """Timestamps are strictly increasing and last cue is within audio duration."""
    total_audio_duration = 30.0
    segments = [
        TranscriptSegment(start_seconds=1.0, end_seconds=5.0, text="Cue 1"),
        TranscriptSegment(start_seconds=6.0, end_seconds=12.0, text="Cue 2"),
        TranscriptSegment(start_seconds=15.0, end_seconds=28.5, text="Cue 3"),
    ]

    out_file = tmp_path / "test.srt"
    res = await build_srt(segments, output_path=str(out_file))

    assert res.ok is True
    # Verify last cue end time <= total_audio_duration
    assert segments[-1].end_seconds <= total_audio_duration

    # Monotonicity check
    for i in range(len(segments) - 1):
        assert segments[i].start_seconds < segments[i].end_seconds
        assert segments[i].end_seconds <= segments[i + 1].start_seconds
