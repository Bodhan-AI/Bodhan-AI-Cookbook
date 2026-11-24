"""Build SRT cues from chunked English transcripts.

The ASR endpoint returns text only, so cue timing inside a chunk is interpolated
by character weight. That limit is reported in result.md, not hidden here.
"""
from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

MAX_LINE = 42
MAX_LINES = 2
MAX_CUE = MAX_LINE * MAX_LINES

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _wrap(text: str) -> list[str]:
    """Greedy word wrap at MAX_LINE; only a single overlong word is split mid-word."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > MAX_LINE:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:MAX_LINE])
            word = word[MAX_LINE:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= MAX_LINE:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _units(text: str) -> list[str]:
    """Split text into pieces that each wrap to at most MAX_LINES lines."""
    units: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            units.append(" ".join(current))
            current.clear()

    for word in text.split():
        if len(word) > MAX_LINE:
            flush()
            units.extend(word[i : i + MAX_LINE] for i in range(0, len(word), MAX_LINE))
            continue
        if len(_wrap(" ".join([*current, word]))) > MAX_LINES:
            flush()
        current.append(word)
    flush()
    return units


def _cue_texts(text: str) -> list[str]:
    normalised = " ".join(str(text).split())
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(normalised):
        if not sentence:
            continue
        if len(_wrap(sentence)) <= MAX_LINES:
            out.append(sentence)
        else:
            out.extend(_units(sentence))
    return out


def cues(chunks: list[dict]) -> list[dict]:
    """One cue per sentence (or per 2-line piece of a long one), weighted by length.

    Each chunk contributes `{"start_s", "end_s", "text"}` cues with text already
    wrapped to <=2 lines of <=42 characters, in order, never overlapping.
    """
    out: list[dict] = []
    for chunk in chunks:
        texts = _cue_texts(chunk.get("text") or "")
        if not texts:
            continue
        start_ms = max(0, round(float(chunk.get("start_s") or 0.0) * 1000))
        end_ms = round(float(chunk.get("end_s") or 0.0) * 1000)
        end_ms = max(end_ms, start_ms + 1)
        span = end_ms - start_ms
        total = sum(len(t) for t in texts) or 1
        cursor = start_ms
        running = 0
        for i, text in enumerate(texts):
            running += len(text)
            if i == len(texts) - 1:
                cue_end = end_ms
            else:
                cue_end = min(end_ms, max(cursor, start_ms + round(span * running / total)))
            out.append(
                {"start_s": cursor / 1000.0, "end_s": cue_end / 1000.0, "text": "\n".join(_wrap(text))}
            )
            cursor = cue_end

    for previous, current in pairwise(out):  # chunks given out of order or overlapping
        if current["start_s"] < previous["end_s"]:
            current["start_s"] = previous["end_s"]
            current["end_s"] = max(current["end_s"], current["start_s"])
    return out


def _timestamp(seconds: float) -> str:
    ms = max(0, round(float(seconds) * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def to_srt(cues: list[dict]) -> str:
    blocks = [
        f"{i}\n{_timestamp(cue['start_s'])} --> {_timestamp(cue['end_s'])}\n{str(cue['text']).strip()}\n"
        for i, cue in enumerate(cues, 1)
    ]
    return "\n".join(blocks)


def write_srt(cues: list[dict], path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_srt(cues), encoding="utf-8")
    return str(target)
