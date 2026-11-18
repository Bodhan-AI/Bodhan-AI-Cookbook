"""Pytest suite for MCP stdio smoke tests and deterministic make_srt tool."""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

from bodhan_mcp.tools.srt import format_srt_time, make_srt, wrap_srt_text
from scripts.mcp_smoke import run_mcp_smoke


# ------------------------------------------------------------------------------
# 1. make_srt Unit Tests
# ------------------------------------------------------------------------------


def test_format_srt_time():
    """Verify millisecond rounding and HH:MM:SS,mmm format."""
    assert format_srt_time(0.0) == "00:00:00,000"
    assert format_srt_time(1.234) == "00:00:01,234"
    assert format_srt_time(65.5) == "00:01:05,500"
    assert format_srt_time(3661.009) == "01:01:01,009"


def test_make_srt_valid_format_and_ordering(tmp_path: Path):
    """Verify valid SRT formatting and chronological ordering of segments."""
    out_file = tmp_path / "subtitles.srt"
    # Pass segments deliberately out of order
    segments = [
        {"start": 20.5, "end": 29.0, "text": "Second sentence of speech."},
        {"start": 0.0, "end": 20.5, "text": "First sentence of speech."},
        {"start": 29.0, "end": 35.123, "text": "Third sentence concluding the audio."},
    ]

    res = make_srt(segments, out_file)
    assert res.success is True
    assert res.cues_count == 3
    assert out_file.exists()

    content = out_file.read_text(encoding="utf-8")
    cues = content.strip().split("\n\n")
    assert len(cues) == 3

    # Check cue 1 (first chronologically)
    lines1 = cues[0].split("\n")
    assert lines1[0] == "1"
    assert lines1[1] == "00:00:00,000 --> 00:00:20,500"
    assert lines1[2] == "First sentence of speech."

    # Check cue 2
    lines2 = cues[1].split("\n")
    assert lines2[0] == "2"
    assert lines2[1] == "00:00:20,500 --> 00:00:29,000"
    assert lines2[2] == "Second sentence of speech."

    # Check cue 3
    lines3 = cues[2].split("\n")
    assert lines3[0] == "3"
    assert lines3[1] == "00:00:29,000 --> 00:00:35,123"
    assert lines3[2] == "Third sentence concluding the audio."


def test_make_srt_long_line_splitting(tmp_path: Path):
    """Verify lines longer than 42 characters are wrapped cleanly at word boundaries."""
    long_text = "This is an exceptionally long subtitle cue line that definitely exceeds forty two characters."
    wrapped = wrap_srt_text(long_text, max_chars_per_line=42)
    lines = wrapped.split("\n")
    assert len(lines) >= 2
    for line in lines:
        assert len(line) <= 42

    # Run through full make_srt
    out_file = tmp_path / "wrapped.srt"
    res = make_srt([{"start": 0.0, "end": 5.0, "text": long_text}], out_file)
    assert res.success is True
    file_content = out_file.read_text(encoding="utf-8")
    assert "-->" in file_content
    for line in file_content.split("\n"):
        if "-->" not in line and not line.isdigit() and line.strip():
            assert len(line) <= 42


def test_make_srt_empty_input_and_invalid_args(tmp_path: Path):
    """Verify structured errors on empty inputs and invalid timestamps."""
    out_file = tmp_path / "bad.srt"

    # Empty segments
    res_empty = make_srt([], out_file)
    assert res_empty.success is False
    assert res_empty.error is not None
    assert res_empty.error.code == "EMPTY_INPUT"

    # End before start
    res_inv = make_srt([{"start": 10.0, "end": 5.0, "text": "Time travel"}], out_file)
    assert res_inv.success is False
    assert res_inv.error is not None
    assert res_inv.error.code == "INVALID_ARGUMENTS"


# ------------------------------------------------------------------------------
# 2. MCP stdio Smoke Test
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_stdio_smoke_all_tools():
    """Verify that every tool returns structured errors on malformed input and server never crashes."""
    smoke_res = await run_mcp_smoke(python_executable=sys.executable)
    assert smoke_res.get("server_alive") is True

    discovered = smoke_res.get("discovered_tools", [])
    expected_tools = {
        "indic_translate",
        "indic_ocr",
        "indic_transcribe",
        "indic_speak",
        "inspect_media",
        "read_text_file",
        "make_srt",
        "detect_script_language",
    }
    assert set(discovered) == expected_tools

    # Check each executed call
    tested_calls = smoke_res.get("tested_calls", [])
    assert len(tested_calls) >= 8
    for call in tested_calls:
        tool = call["tool"]
        output = call["output"]
        if tool in ("indic_translate", "indic_ocr", "indic_transcribe", "indic_speak", "make_srt"):
            assert output.get("success") is False or "error" in output
