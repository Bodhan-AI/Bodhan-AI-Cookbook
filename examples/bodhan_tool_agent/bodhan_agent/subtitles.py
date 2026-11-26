"""Render SubRip cues from measured transcription window boundaries."""

from math import isfinite

from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.transcription import TranscriptionResult

SUBTITLE_WINDOW_SECONDS = 8
SUBTITLE_TIMING_NOTE = (
    "Subtitle cues use audio window boundaries that favor nearby pauses. "
    "Speech within each cue can start or end at a different time, and some cuts may split words."
)


def _timestamp(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def render_srt(transcript: TranscriptionResult, captions: dict[int, str]) -> str:
    """Require exactly one cue for every nonblank window and increasing audio times."""
    expected = {index for index, window in enumerate(transcript.windows) if window.text.strip()}
    if not expected or captions.keys() != expected:
        raise ToolError("The subtitle cues do not cover all transcribed speech windows.")

    previous_end = 0.0
    cues = []
    for index, window in enumerate(transcript.windows):
        start, end = window.start_seconds, window.end_seconds
        if not (isfinite(end) and previous_end == start < end <= transcript.duration_seconds):
            raise ToolError("Transcription window boundaries are inconsistent.")
        previous_end = end
        if index not in captions:
            continue
        text = " ".join(captions[index].split())
        if not text or "\x00" in text:
            raise ToolError("A subtitle cue has invalid text.")
        start_ms = round(start * 1000)
        end_ms = max(start_ms + 1, round(end * 1000))
        cues.append(f"{len(cues) + 1}\n{_timestamp(start_ms)} --> {_timestamp(end_ms)}\n{text}")
    if previous_end != transcript.duration_seconds:
        raise ToolError("Transcription windows do not cover the recording duration.")
    return "\n\n".join(cues) + "\n"
