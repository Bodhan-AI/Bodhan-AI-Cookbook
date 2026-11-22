"""Build SRT subtitle files from timed segments. Plain code."""
from __future__ import annotations

import textwrap

MIN_CUE_SECONDS = 0.5


def format_timestamp(seconds: float) -> str:
    """Seconds -> 'HH:MM:SS,mmm' as SRT requires."""
    total_ms = max(0, round(seconds * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def segments_to_srt(segments: list[dict], max_chars_per_line: int = 42) -> str:
    """Convert [{start, end, text}, ...] to SRT text.

    Empty cues are skipped, cues are numbered from 1, a cue shorter than 0.5 s is
    stretched, and a cue that would overlap the previous one is moved to start after it.
    """
    cues: list[str] = []
    previous_end = 0.0
    for seg in segments:
        text = " ".join(str(seg.get("text", "")).split())
        if not text:
            continue
        start = max(float(seg["start"]), previous_end)
        end = max(float(seg["end"]), start + MIN_CUE_SECONDS)
        lines = textwrap.wrap(text, width=max_chars_per_line) or [text]
        cues.append(
            f"{len(cues) + 1}\n{format_timestamp(start)} --> {format_timestamp(end)}\n" + "\n".join(lines)
        )
        previous_end = end
    return "\n\n".join(cues) + ("\n" if cues else "")
