"""Subtitle builder: timed transcript segments -> SRT file, optionally translated, with no text passing through the caller.

``transcribe_audio`` saves timed segments to a JSON file; this tool reads that file, cuts each segment into short
cues (time shared out by text length), translates every cue with Indic-Translate when a target language is given,
and writes the SRT. The caller handles file paths only, so long transcripts never enter the LLM's context and
the LLM never has to copy Indian-language text or invent timestamps.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import BaseModel

from bodhan_core.chunking import split_text
from bodhan_core.errors import ToolError, check_input_file
from bodhan_core.runtime import BACKOFF_S
from bodhan_mcp.tools.files import write_text_file
from bodhan_mcp.tools.translate import TRANSLATE_LANGUAGES, translate

CUE_CHARS = 100  # longest cue before a segment is cut into more cues


class SubtitleResult(BaseModel):
    ok: bool
    output_path: str | None = None
    cues: int = 0  # entries written to the SRT
    segments_in: int = 0  # timed segments read (silent ones produce no cue)
    target_language: str | None = None  # null: subtitles are in the spoken language, untranslated
    cost_inr: float = 0.0
    error: ToolError | None = None


def _fail(code: str, message: str, cost_inr: float = 0.0, retryable: bool = False) -> SubtitleResult:
    return SubtitleResult(ok=False, cost_inr=cost_inr, error=ToolError(code=code, message=message, retryable=retryable))


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _cues(segments: list[dict]) -> list[tuple[float, float, str]]:
    """Cut each segment into short pieces; each piece gets a share of the segment's time proportional to its length."""
    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        pieces = [p.strip() for p in split_text(seg["text"], CUE_CHARS) if p.strip()]
        total = sum(len(p) for p in pieces)
        t = float(seg["start_s"])
        span = float(seg["end_s"]) - t
        for p in pieces:
            end = t + span * len(p) / total
            cues.append((t, end, p))
            t = end
    return cues


async def make_subtitles(
    segments_path: str,
    output_path: str,
    target_language: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    backoff_s: float = BACKOFF_S,
) -> SubtitleResult:
    path = Path(segments_path)
    if err := check_input_file(path, "segments_path", "JSON segments (the `segments_path` returned by transcribe_audio)"):
        return SubtitleResult(ok=False, error=err)
    if Path(output_path).suffix.lower() != ".srt":
        return _fail("bad_output_path", f"`output_path` `{output_path}` must end in .srt. Example: 'artifacts/subtitles.srt'.")
    if target_language is not None and target_language not in TRANSLATE_LANGUAGES:
        return _fail("unsupported_language", f"`{target_language}` is not supported for subtitles. Use one of: {', '.join(sorted(TRANSLATE_LANGUAGES))}, or omit it for untranslated subtitles.")
    try:
        segments = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(segments, list) and all({"start_s", "end_s", "text"} <= s.keys() for s in segments)
    except (ValueError, AssertionError, AttributeError, OSError):
        return _fail("bad_segments_file", f"`{path.name}` is not a segments file. Pass the `segments_path` returned by `transcribe_audio`.")
    segments = [s for s in segments if str(s["text"]).strip()]
    if not segments:
        return _fail("no_speech", "The segments file has no spoken text (the audio may be silent). Nothing to subtitle.")

    cues = _cues(segments)
    cost = 0.0
    lines: list[str] = []
    for i, (start, end, text) in enumerate(cues, 1):
        if target_language:
            res = await translate(text, target_language, None, transport=transport, backoff_s=backoff_s)
            cost += res.cost_inr
            if not res.ok:
                return _fail(res.error.code, f"Cue {i} of {len(cues)} failed ({i - 1} translated before it): {res.error.message}", round(cost, 6), res.error.retryable)
            text = (res.translation or "").strip()
            if not text:
                return _fail("empty_translation", f"Cue {i} of {len(cues)} came back empty from Indic-Translate. Retry the call.", round(cost, 6), True)
        lines.append(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")

    written = write_text_file(output_path, "\n".join(lines))
    if not written.ok:
        return SubtitleResult(ok=False, cost_inr=round(cost, 6), error=written.error)
    return SubtitleResult(ok=True, output_path=written.output_path, cues=len(cues), segments_in=len(segments), target_language=target_language, cost_inr=round(cost, 6))
