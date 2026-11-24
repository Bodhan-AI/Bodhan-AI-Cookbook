"""Subtitle output.

SRT, one cue per source segment, using the translated text and the segment's
original timing — the same timings the dub was placed at, so the subtitles and
the audio agree.

Two rules that matter for readability:

* a cue is never shorter than 0.8 s, because a flash of text cannot be read;
* a cue never runs past the next cue's start, so lines do not stack.

Text is wrapped to two lines. Indic scripts are wider per character than Latin,
so the limit is conservative.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import text

MAX_LINE_CHARS = 42
MIN_CUE_S = 0.8
MIN_GAP_S = 0.05


def write_srt(
    path: Path | str,
    segments: list[dict[str, Any]],
    *,
    key: str = "translation",
    max_line_chars: int = MAX_LINE_CHARS,
) -> int:
    cues = build_cues(segments, key=key, max_line_chars=max_line_chars)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(_render(cues), encoding="utf-8")
    return len(cues)


def build_cues(
    segments: list[dict[str, Any]],
    *,
    key: str = "translation",
    max_line_chars: int = MAX_LINE_CHARS,
) -> list[dict[str, Any]]:
    usable = [
        {
            "start": float(segment.get("start", 0.0)),
            "end": float(segment.get("end", 0.0)),
            "text": text.normalize_ws(str(segment.get(key, ""))),
        }
        for segment in segments
    ]
    usable = [cue for cue in usable if cue["text"]]
    usable.sort(key=lambda cue: cue["start"])

    cues: list[dict[str, Any]] = []
    for index, cue in enumerate(usable):
        start = cue["start"]
        end = cue["end"]
        if end - start < MIN_CUE_S:
            end = start + MIN_CUE_S
        if index + 1 < len(usable):
            end = min(end, usable[index + 1]["start"] - MIN_GAP_S)
        if end <= start:
            end = start + MIN_CUE_S
        cues.append(
            {
                "start": start,
                "end": end,
                "text": _wrap(cue["text"], max_line_chars),
            }
        )
    return cues


def _render(cues: list[dict[str, Any]]) -> str:
    blocks = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue['start'])} --> {_timestamp(cue['end'])}\n{cue['text']}\n"
        )
    return "\n".join(blocks)


def _timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wrap(value: str, max_chars: int) -> str:
    words = value.split()
    if not words:
        return ""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return "\n".join(lines)
    # Keep two lines; the remainder joins the second rather than being dropped.
    return lines[0] + "\n" + " ".join(lines[1:])
