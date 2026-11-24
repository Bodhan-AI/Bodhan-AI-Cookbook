"""
Transcribe tool — speech-to-text using Bodhan Indic-Transcribe.

Handles:
- Audio chunking for files > 30 seconds
- Multiple audio formats (WAV, MP3, FLAC, OGG)
- Language detection / language hint
- Timestamp-aware segment merging
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from bodhan_mcp.client import BodhanClient, BodhanAPIError
from bodhan_mcp.media import chunk_audio, get_audio_duration, is_audio, SUPPORTED_AUDIO_FORMATS

logger = logging.getLogger(__name__)


async def transcribe_audio(
    client: BodhanClient,
    file_path: str,
    language: str | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Transcribe an audio file to text.

    Automatically chunks audio longer than 30 seconds and merges the results
    into a single transcript with segment timestamps.

    Args:
        client: BodhanClient instance.
        file_path: Path to the audio file (WAV, MP3, FLAC, or OGG).
        language: Optional ISO 639-1 language hint (e.g., 'hi', 'ta', 'bn').
                  If None, the model auto-detects.
        output_dir: Directory for temporary chunk files. Uses system temp if None.

    Returns:
        dict with:
            - text: Full transcribed text
            - segments: List of {start, end, text} for each chunk
            - language: Detected or hinted language
            - duration_seconds: Total audio duration
            - num_chunks: Number of chunks processed

    Raises:
        BodhanAPIError: If the API call fails.
        FileNotFoundError: If the audio file doesn't exist.
        ValueError: If the file format is unsupported.
    """
    path = Path(file_path)

    # Validate
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    if not is_audio(file_path):
        raise ValueError(
            f"Unsupported audio format: {path.suffix}. "
            f"Supported: {', '.join(sorted(SUPPORTED_AUDIO_FORMATS))}"
        )

    # Get duration
    total_duration = get_audio_duration(str(path))
    logger.info(f"Transcribing {path.name} ({total_duration:.1f}s)")

    # Chunk if needed
    chunks = chunk_audio(str(path), output_dir=output_dir)
    logger.info(f"Processing {len(chunks)} chunk(s)")

    # Transcribe each chunk
    segments = []
    full_text_parts = []

    for chunk in chunks:
        result = await client.transcribe(
            audio_path=chunk["path"],
            language=language,
            duration_seconds=chunk["duration_seconds"],
        )

        segment = {
            "start_seconds": chunk["start_seconds"],
            "end_seconds": chunk["end_seconds"],
            "text": result["text"].strip(),
        }
        segments.append(segment)
        full_text_parts.append(result["text"].strip())

    full_text = " ".join(full_text_parts)

    return {
        "text": full_text,
        "segments": segments,
        "language": language or "auto-detected",
        "duration_seconds": round(total_duration, 2),
        "num_chunks": len(chunks),
    }


def generate_srt(segments: list[dict[str, Any]], offset: float = 0.0) -> str:
    """
    Generate SRT subtitle content from transcription segments.

    Args:
        segments: List of {start_seconds, end_seconds, text} dicts.
        offset: Time offset to add to all timestamps.

    Returns:
        SRT formatted string.
    """
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_srt_time(seg["start_seconds"] + offset)
        end = _format_srt_time(seg["end_seconds"] + offset)
        text = seg["text"]
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
