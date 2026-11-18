"""Free helpers: make_srt (segments -> SubRip subtitles) and read_text (bounded reads of large files)."""

from __future__ import annotations

import json
import textwrap
from datetime import timedelta

import srt

from bodhan_core import workspace
from bodhan_core.errors import ErrorCode, ToolError

MAX_READ = 20_000
MAX_LINES = 2  # subtitle convention: at most two lines on screen


def _load_segments(segments_path: str) -> tuple[list[dict], object]:
    p = workspace.resolve_input(segments_path)
    try:
        segments = json.loads(workspace.read_text_file(p))
        if not isinstance(segments, list):
            raise ValueError
        for s in segments:
            float(s["start"]), float(s["end"]), str(s["text"])
    except (ValueError, KeyError, TypeError):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{p.name} is not a segments file ([{{start, end, text}}, ...]).",
                        hint="Pass the segments_path returned by transcribe_audio or translate_segments.") from None
    return segments, p


async def make_srt(segments_path: str, max_chars_per_line: int = 42, out_dir: str | None = None) -> dict:
    if not 10 <= max_chars_per_line <= 120:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "max_chars_per_line must be between 10 and 120.")
    segments, p = _load_segments(segments_path)
    cues, fixes, prev_end = [], 0, 0.0
    for s in sorted(segments, key=lambda x: float(x["start"])):
        text = " ".join(str(s["text"]).split())
        if not text:
            continue
        start, end = max(float(s["start"]), prev_end), float(s["end"])
        if start != float(s["start"]):
            fixes += 1
        if end <= start:
            end = start + 0.5
            fixes += 1
        lines = textwrap.wrap(text, max_chars_per_line) or [text]
        groups = [lines[i : i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]
        total = sum(len(" ".join(g)) for g in groups) or 1
        t = start
        for g in groups:
            share = (end - start) * len(" ".join(g)) / total
            cues.append(srt.Subtitle(index=len(cues) + 1, start=timedelta(seconds=t),
                                     end=timedelta(seconds=t + share), content="\n".join(g)))
            t += share
        prev_end = end
    if not cues:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "No non-empty segments to write.")
    body = srt.compose(cues)
    out = workspace.output_dir(out_dir)
    srt_path = workspace.artifact_path(out, p, ".srt", key=str(max_chars_per_line))
    srt_path.write_text(body, encoding="utf-8")
    parsed = list(srt.parse(body))  # round-trip check
    return {
        "ok": True,
        "srt_path": str(srt_path),
        "cues": len(parsed),
        "duration_s": round(parsed[-1].end.total_seconds(), 2),
        "timing_fixes": fixes,
        "valid": all(a.end <= b.start for a, b in zip(parsed, parsed[1:], strict=False)),
        "preview": workspace.preview(body),
    }


async def read_text(path: str, offset: int = 0, length: int = 4000) -> dict:
    if offset < 0 or not 1 <= length <= MAX_READ:
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"offset must be ≥ 0 and length between 1 and {MAX_READ}.")
    p = workspace.resolve_input(path)
    if workspace.media_kind(p) != "text":
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, "read_text reads text files only.",
                        hint="Use ocr_document for images/PDFs and transcribe_audio for audio.")
    text = workspace.read_text_file(p)
    chunk = text[offset : offset + length]
    end = offset + len(chunk)
    return {
        "ok": True,
        "path": str(p),
        "offset": offset,
        "end": end,
        "total_chars": len(text),
        "has_more": end < len(text),
        "text": chunk,
        "untrusted_content": True,
    }
