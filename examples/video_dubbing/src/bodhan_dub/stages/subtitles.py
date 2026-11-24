"""
Subtitle and transcript writer: SRT, VTT, and transcript.en.json.
Enforces grapheme-safe line wrapping and sample-accurate timestamp formatting.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from bodhan_dub.stages.segment import SegmentSlot
from bodhan_dub.text.graphemes import wrap_text_graphemes


def format_srt_timestamp(seconds: float) -> str:
    """Formats seconds into SRT timestamp HH:MM:SS,mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_vtt_timestamp(seconds: float) -> str:
    """Formats seconds into WebVTT timestamp HH:MM:SS.mmm."""
    total_ms = max(0, int(round(seconds * 1000)))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def write_srt(segments: List[SegmentSlot], out_path: Path) -> Path:
    """Writes standard SRT subtitle file with grapheme-safe line wrapping."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    for i, seg in enumerate(segments, 1):
        start_ts = format_srt_timestamp(seg.start)
        end_ts = format_srt_timestamp(seg.end)
        wrapped_text = wrap_text_graphemes(seg.text, max_line_chars=42, max_lines=2)

        lines.append(f"{i}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(wrapped_text)
        lines.append("")  # Empty separator line

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_vtt(segments: List[SegmentSlot], out_path: Path) -> Path:
    """Writes standard WebVTT subtitle file."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = ["WEBVTT", ""]
    for i, seg in enumerate(segments, 1):
        start_ts = format_vtt_timestamp(seg.start)
        end_ts = format_vtt_timestamp(seg.end)
        wrapped_text = wrap_text_graphemes(seg.text, max_line_chars=42, max_lines=2)

        lines.append(f"{i}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(wrapped_text)
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_transcript_en_json(segments: List[SegmentSlot], out_path: Path) -> Path:
    """Writes structured transcript.en.json containing all segment metadata."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = [seg.model_dump() for seg in segments]
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def write_target_segments_json(segments_data: list, out_path: Path) -> Path:
    """Writes <lang>/segments.<lang>.json containing dubbed segment details."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(segments_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def write_target_subtitles(
    segments_data: list,
    srt_path: Path,
    vtt_path: Path,
    omit_skipped: bool = True,
) -> None:
    """Writes both SRT and VTT subtitle files for target language using placed timestamps."""
    srt_path = Path(srt_path)
    vtt_path = Path(vtt_path)
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    vtt_path.parent.mkdir(parents=True, exist_ok=True)

    srt_lines: List[str] = []
    vtt_lines: List[str] = ["WEBVTT", ""]

    cue_idx = 0
    for seg in segments_data:
        # B6: Do not emit Indic subtitle cues for skipped or translation_failed slots
        status = seg.get("status", "")
        fit_status = seg.get("fit_status", "")
        qa_status = seg.get("qa_status", "")
        if omit_skipped and (
            status in ("skipped", "translation_failed")
            or fit_status == "skipped"
            or qa_status == "skipped"
        ):
            continue

        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        text = str(seg.get("target_text") or seg.get("translated_text", "")).strip()

        if not text or end <= start:
            continue

        cue_idx += 1
        wrapped = wrap_text_graphemes(text, max_line_chars=40, max_lines=2)

        start_srt = format_srt_timestamp(start)
        end_srt = format_srt_timestamp(end)
        srt_lines.append(f"{cue_idx}")
        srt_lines.append(f"{start_srt} --> {end_srt}")
        srt_lines.append(wrapped)
        srt_lines.append("")

        start_vtt = format_vtt_timestamp(start)
        end_vtt = format_vtt_timestamp(end)
        vtt_lines.append(f"{cue_idx}")
        vtt_lines.append(f"{start_vtt} --> {end_vtt}")
        vtt_lines.append(wrapped)
        vtt_lines.append("")

    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
    vtt_path.write_text("\n".join(vtt_lines), encoding="utf-8")
