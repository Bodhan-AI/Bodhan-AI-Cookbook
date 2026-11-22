"""
Transcribe audio using Indic-Transcribe.

Handles files of any length by chunking into ≤28 s pieces (safely under the
30 s API limit), running chunks concurrently, and stitching the results with
corrected timestamps.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..clients.bodhan_client import call_transcribe
from .audio_chunk import chunk_audio

_MAX_CHUNK_S = 28.0


async def transcribe_audio(
    audio_path: Path,
    language: str | None = None,
) -> dict:
    """Transcribe an audio file of any length.

    Args:
        audio_path: Path to the audio file (any format ffmpeg can read).
        language:   BCP-47 language hint. If None the model auto-detects.

    Returns:
        {
          "text": str,
          "language": str,
          "segments": [{"start": float, "end": float, "text": str}, ...],
          "cost_inr": float,
        }
    """
    chunks = chunk_audio(Path(audio_path), max_seconds=_MAX_CHUNK_S)
    results = await asyncio.gather(
        *[call_transcribe(c, language=language) for c in chunks]
    )

    combined_text_parts: list[str] = []
    all_segments: list[dict] = []
    total_cost = 0.0
    detected_lang = language or "unknown"
    time_offset = 0.0

    for chunk_result, cost in results:
        combined_text_parts.append(chunk_result["text"])
        total_cost += cost
        if chunk_result.get("language") and detected_lang == "unknown":
            detected_lang = chunk_result["language"]

        for seg in chunk_result.get("segments", []):
            all_segments.append(
                {
                    "start": seg["start"] + time_offset,
                    "end": seg["end"] + time_offset,
                    "text": seg["text"],
                }
            )
        if all_segments:
            time_offset = all_segments[-1]["end"]

    return {
        "text": " ".join(combined_text_parts).strip(),
        "language": detected_lang,
        "segments": all_segments,
        "cost_inr": round(total_cost, 4),
    }
