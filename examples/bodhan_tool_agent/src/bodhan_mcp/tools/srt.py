"""Deterministic SRT subtitle generation helper tool for Bodhan MCP Server.

Plain code implementation (no LLM, no model):
- Accepts a list of segments with start, end, and text.
- Validates and sorts segments by start time.
- Rounds timestamps to milliseconds and formats as HH:MM:SS,mmm.
- Splits lines exceeding 42 characters at word boundaries.
- Emits standard SubRip (.srt) subtitle format.

Note: Bodhan's Indic-Transcribe returns plain text transcripts without word-level
or sentence-level timestamps. Start and end timestamps are derived from our own audio
chunk boundaries (up to 29 seconds per cue).
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

from bodhan_mcp.common import StructuredError


class SrtSegment(BaseModel):
    start: float = Field(..., description="Start time in seconds.")
    end: float = Field(..., description="End time in seconds.")
    text: str = Field(..., description="Subtitle text for this cue.")


class SrtOutput(BaseModel):
    success: bool
    output_file_path: str = ""
    cues_count: int = 0
    approximation_note: Optional[str] = None
    error: Optional[StructuredError] = None


def format_srt_time(seconds: float) -> str:
    """Formats floating-point seconds into SRT timestamp HH:MM:SS,mmm with millisecond rounding."""
    total_ms = max(0, int(round(seconds * 1000.0)))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_srt_text(text: str, max_chars_per_line: int = 42) -> str:
    """Wraps cue text so no line exceeds max_chars_per_line (default 42), breaking at word boundaries."""
    if not text:
        return ""

    lines: List[str] = []
    for raw_line in text.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        words = raw_line.split()
        cur_line: List[str] = []
        cur_len = 0
        for w in words:
            if len(w) > max_chars_per_line:
                if cur_line:
                    lines.append(" ".join(cur_line))
                    cur_line = []
                    cur_len = 0
                for chunk in [
                    w[i : i + max_chars_per_line] for i in range(0, len(w), max_chars_per_line)
                ]:
                    lines.append(chunk)
                continue

            added_len = len(w) if not cur_line else len(w) + 1
            if cur_len + added_len <= max_chars_per_line:
                cur_line.append(w)
                cur_len += added_len
            else:
                lines.append(" ".join(cur_line))
                cur_line = [w]
                cur_len = len(w)
        if cur_line:
            lines.append(" ".join(cur_line))

    return "\n".join(lines)


def make_srt(
    segments: Optional[Union[List[Union[Dict[str, Any], SrtSegment]], Dict[str, Any], str]] = None,
    output_path: Optional[str | Path] = None,
    transcript: Optional[Union[Dict[str, Any], str]] = None,
    text: Optional[str] = None,
) -> SrtOutput:
    """Generates an SRT subtitle file from timed transcript segments or transcript references.

    Accepts either:
    1. transcript reference (@stepN) with text (@stepM.text): builds cues deterministically
       from transcribe chunk boundaries. If text is one block and there are several chunks,
       splits it at sentence boundaries in proportion to chunk durations.
    2. segments: list of segment dictionaries with 'start', 'end', and 'text'.
    """
    # Parameter normalization: handle case where output_path was passed as first arg or kwargs
    if output_path is None:
        if isinstance(segments, (str, Path)) and not isinstance(segments, list):
            output_path = segments
            segments = None
        else:
            return SrtOutput(
                success=False,
                error=StructuredError(
                    code="INVALID_ARGUMENTS",
                    message="Missing required parameter 'output_path'.",
                    retryable=False,
                ),
            )

    # If segments was passed a dict/str instead of list, treat as transcript
    if segments is not None and not isinstance(segments, list):
        if transcript is None:
            transcript = segments
            segments = None

    approximation_note: Optional[str] = None

    # Handle transcript reference mode
    if transcript is not None or (segments is None and text is not None):
        parsed_transcript: Dict[str, Any] = {}
        if isinstance(transcript, str):
            try:
                parsed_transcript = json.loads(transcript)
            except Exception:
                parsed_transcript = {"text": transcript}
        elif isinstance(transcript, dict):
            parsed_transcript = transcript

        full_text = (
            text
            if text is not None
            else (parsed_transcript.get("text") or parsed_transcript.get("transcript") or "")
        )
        chunks = parsed_transcript.get("chunks") or []

        chunk_bounds: List[Tuple[float, float]] = []
        if isinstance(chunks, list) and len(chunks) > 0:
            for c in chunks:
                if isinstance(c, dict):
                    s = float(c.get("start_second", 0.0))
                    e = float(c.get("end_second", 0.0))
                    chunk_bounds.append((s, e))

        if not chunk_bounds:
            dur = float(parsed_transcript.get("duration_seconds") or 0.0)
            if dur > 0.0:
                chunk_bounds = [(0.0, dur)]
            else:
                chunk_bounds = [(0.0, 5.0)]

        k = len(chunk_bounds)
        clean_text = str(full_text).strip()

        if k == 1:
            segments = [
                {"start": chunk_bounds[0][0], "end": chunk_bounds[0][1], "text": clean_text}
            ]
        else:
            # Multi-chunk: split clean_text at sentence boundaries in proportion to chunk durations
            durations = [max(e - s, 0.1) for s, e in chunk_bounds]
            total_dur = sum(durations)

            raw_sentences = [
                s.strip() for s in re.split(r"(?<=[.!?।॥\n])\s+", clean_text) if s.strip()
            ]
            if not raw_sentences:
                raw_sentences = [clean_text]

            # If fewer sentences than chunks, split further at word boundaries
            if len(raw_sentences) < k:
                words = clean_text.split()
                if len(words) >= k:
                    word_groups = []
                    curr_w = 0
                    for i, d in enumerate(durations):
                        if i == k - 1:
                            sub_w = words[curr_w:]
                        else:
                            count = max(1, int(round(len(words) * (d / total_dur))))
                            sub_w = words[curr_w : curr_w + count]
                            curr_w += count
                        word_groups.append(" ".join(sub_w))
                    grouped_texts = word_groups
                else:
                    grouped_texts = raw_sentences + [""] * (k - len(raw_sentences))
            elif len(raw_sentences) == k:
                grouped_texts = raw_sentences
            else:
                # More sentences than chunks: allocate sentences to buckets in proportion to duration
                grouped_texts = []
                sent_idx = 0
                total_chars = sum(len(s) for s in raw_sentences)
                for i, d in enumerate(durations):
                    if i == k - 1:
                        bucket = raw_sentences[sent_idx:]
                    else:
                        target_chars = total_chars * (d / total_dur)
                        bucket = []
                        cur_b_len = 0
                        max_avail = len(raw_sentences) - (k - 1 - i)
                        while sent_idx < max_avail:
                            s_candidate = raw_sentences[sent_idx]
                            if bucket and (cur_b_len + len(s_candidate) > target_chars):
                                break
                            bucket.append(s_candidate)
                            cur_b_len += len(s_candidate)
                            sent_idx += 1
                        if not bucket and sent_idx < len(raw_sentences):
                            bucket.append(raw_sentences[sent_idx])
                            sent_idx += 1
                    grouped_texts.append(" ".join(bucket))

            segments = [
                {"start": chunk_bounds[i][0], "end": chunk_bounds[i][1], "text": grouped_texts[i]}
                for i in range(k)
            ]
            approximation_note = f"Subtitles timed using proportional sentence-boundary alignment across {k} audio chunk boundaries."

    if not segments:
        return SrtOutput(
            success=False,
            output_file_path=str(output_path),
            cues_count=0,
            approximation_note=approximation_note,
            error=StructuredError(
                code="EMPTY_INPUT",
                message="Segments list is empty. At least one timed cue is required.",
                retryable=False,
            ),
        )

    # Normalize and validate segments
    normalized: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments, 1):
        if isinstance(seg, SrtSegment):
            data = seg.model_dump()
        elif isinstance(seg, dict):
            data = seg
        else:
            return SrtOutput(
                success=False,
                output_file_path=str(output_path),
                approximation_note=approximation_note,
                error=StructuredError(
                    code="INVALID_ARGUMENTS",
                    message=f"Segment #{idx} is not a valid dictionary or SrtSegment.",
                    retryable=False,
                ),
            )

        start = data.get("start")
        end = data.get("end")
        text_val = str(data.get("text", "")).strip()

        if start is None or end is None:
            return SrtOutput(
                success=False,
                output_file_path=str(output_path),
                approximation_note=approximation_note,
                error=StructuredError(
                    code="INVALID_ARGUMENTS",
                    message=f"Segment #{idx} is missing 'start' or 'end' time.",
                    retryable=False,
                ),
            )

        try:
            start_f = float(start)
            end_f = float(end)
        except (ValueError, TypeError):
            return SrtOutput(
                success=False,
                output_file_path=str(output_path),
                approximation_note=approximation_note,
                error=StructuredError(
                    code="INVALID_ARGUMENTS",
                    message=f"Segment #{idx} start and end must be numeric values.",
                    retryable=False,
                ),
            )

        if end_f < start_f:
            return SrtOutput(
                success=False,
                output_file_path=str(output_path),
                approximation_note=approximation_note,
                error=StructuredError(
                    code="INVALID_ARGUMENTS",
                    message=f"Segment #{idx} end time ({end_f}) cannot be earlier than start time ({start_f}).",
                    retryable=False,
                ),
            )

        normalized.append({"start": start_f, "end": end_f, "text": text_val})

    # Sort segments by start time
    sorted_segments = sorted(normalized, key=lambda s: s["start"])

    # Build SRT cues
    cues: List[str] = []
    for idx, cue in enumerate(sorted_segments, 1):
        start_str = format_srt_time(cue["start"])
        end_str = format_srt_time(cue["end"])
        wrapped_text = wrap_srt_text(cue["text"], max_chars_per_line=42)
        cue_block = f"{idx}\n{start_str} --> {end_str}\n{wrapped_text}"
        cues.append(cue_block)

    srt_content = "\n\n".join(cues) + "\n"

    dest = Path(output_path).resolve()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(srt_content, encoding="utf-8")
    except Exception as e:
        return SrtOutput(
            success=False,
            output_file_path=str(dest),
            approximation_note=approximation_note,
            error=StructuredError(
                code="FILE_WRITE_ERROR",
                message=f"Failed to write SRT file to '{dest}': {str(e)}",
                retryable=False,
            ),
        )

    return SrtOutput(
        success=True,
        output_file_path=str(dest),
        cues_count=len(cues),
        approximation_note=approximation_note,
    )
