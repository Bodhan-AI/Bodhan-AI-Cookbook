"""Free helper tools in plain code: make_subtitles and read_text."""

from __future__ import annotations

import json

from ..errors import BodhanToolError
from ..media import read_text_file, resolve_input
from ..models import ReadResult, SubtitleResult
from ..text import detect_script, pack, split_sentences
from .common import output_dir, output_path, write_text


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(max(seconds, 0) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > 2:  # keep cues to two lines
        half = (len(lines) + 1) // 2
        lines = [" ".join(lines[:half]), " ".join(lines[half:])]
    return "\n".join(lines)


def build_cues(segments: list[dict], max_chars: int = 84) -> list[tuple[float, float, str]]:
    """Split long segments into cue-sized pieces, sharing the segment's time span by character count."""
    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        pieces: list[str] = []
        for sentence in split_sentences(text) or [text]:
            if len(sentence) <= max_chars:
                pieces.append(sentence)
            else:
                pieces.extend(pack(sentence.split(), max_chars))
        pieces = pack(pieces, max_chars)
        total = sum(len(p) for p in pieces) or 1
        t = start
        for p in pieces:
            span = (end - start) * len(p) / total
            cues.append((t, t + span, p))
            t += span
    return cues


async def make_subtitles(segments_path: str, format: str = "srt", output_dir_: str | None = None) -> SubtitleResult:
    src = resolve_input(segments_path)
    if format not in {"srt", "vtt"}:
        raise BodhanToolError("invalid_input", "format must be 'srt' or 'vtt'.")
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
        segments = doc["segments"] if isinstance(doc, dict) else doc
        _ = [(float(s["start"]), float(s["end"])) for s in segments]
    except (ValueError, KeyError, TypeError) as exc:
        raise BodhanToolError(
            "invalid_input",
            "segments_path must be a JSON segments file from transcribe_audio or translate_text.",
            hint='It needs {"segments": [{"start": s, "end": s, "text": ...}]}.',
        ) from exc
    cues = build_cues(segments)
    if not cues:
        raise BodhanToolError("invalid_input", "No segment has any text; nothing to subtitle.")
    sep = "," if format == "srt" else "."
    blocks = [f"{i}\n{_ts(a, sep)} --> {_ts(b, sep)}\n{_wrap(t)}" for i, (a, b, t) in enumerate(cues, 1)]
    body = ("WEBVTT\n\n" if format == "vtt" else "") + "\n\n".join(blocks) + "\n"
    dest = write_text(
        output_path(output_dir(output_dir_), src.stem, "subtitles", format, {"src": str(src), "n": len(cues)}), body
    )
    return SubtitleResult(subtitle_path=str(dest), format=format, cues=len(cues), duration_s=round(cues[-1][1], 2))


async def read_text(path: str, offset: int = 0, max_chars: int = 8000) -> ReadResult:
    src = resolve_input(path)
    if src.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".mp4", ".m4a", ".ogg"}:
        raise BodhanToolError(
            "unsupported_file_type",
            f"{src.name} is not a text file.",
            hint="Use ocr_document or transcribe_audio first.",
        )
    if offset < 0 or not (1 <= max_chars <= 50_000):
        raise BodhanToolError("invalid_input", "offset must be >= 0 and max_chars between 1 and 50000.")
    text = read_text_file(src)
    chunk = text[offset : offset + max_chars]
    nxt = offset + max_chars if offset + max_chars < len(text) else None
    return ReadResult(
        path=str(src),
        content=chunk,
        offset=offset,
        next_offset=nxt,
        total_chars=len(text),
        detected_script=detect_script(text).dominant,
    )
