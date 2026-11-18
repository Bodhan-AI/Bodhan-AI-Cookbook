"""Indic-Transcribe Tool for Bodhan MCP.

Converts speech audio to text using Bodhan's Indic-Transcribe (Core/Flex).
Handles automatic windowing and merging for audio files exceeding 30 seconds.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from bodhan_mcp.cache import cache
from bodhan_mcp.clients.bodhan_client import bodhan_client
from bodhan_mcp.config import config, is_supported_language, normalize_language_code
from bodhan_mcp.media_utils import chunk_audio, inspect_audio, validate_file_reference

logger = logging.getLogger("bodhan.tools.transcribe")


async def transcribe_audio(
    audio_path: Union[str, Path],
    language: str = "auto",
    model_variant: str = "core",
) -> Dict[str, Any]:
    """Transcribe speech audio into text using Bodhan Indic-Transcribe.
    
    Args:
        audio_path: Path to the local audio file (.wav, .mp3, .ogg, .flac).
        language: Language code or name (e.g. 'hi', 'kn', 'ta', or 'auto').
        model_variant: 'core' (native script) or 'flex' (mixed-script/romanized).
        
    Returns:
        Structured dictionary with unified transcript, duration, chunk count, and INR cost.
    """
    path = validate_file_reference(audio_path, must_exist=True)

    # Validate language if specified
    norm_lang = "auto"
    if language and language.lower() != "auto":
        norm_lang = normalize_language_code(language)
        if not norm_lang:
            raise ValueError(
                f"Unsupported language '{language}' requested for Indic-Transcribe. "
                f"Bodhan only supports the 22 scheduled Indian languages and English."
            )

    # Check cache for overall file
    params = {"tool": "transcribe", "language": norm_lang, "model_variant": model_variant}
    cache_key = cache.generate_key("transcribe", params, path)
    cached_result = cache.get(cache_key)
    if cached_result:
        cached_result["cached"] = True
        cached_result["cost_inr"] = 0.0  # Zero cost on cache hit
        return cached_result

    # Inspect audio metadata
    inspection = inspect_audio(path)
    total_duration = inspection["duration_seconds"]

    # Window audio if duration > 30 seconds
    chunks = chunk_audio(path, max_duration_sec=config.max_audio_chunk_duration_sec)
    transcript_parts = []

    for chunk in chunks:
        chunk_file = Path(chunk["file_path"])
        res = await bodhan_client.transcribe(
            audio_path=chunk_file,
            language=norm_lang,
            model_variant=model_variant,
        )
        text = res.get("text", "").strip()
        if text:
            transcript_parts.append(text)

    unified_transcript = " ".join(transcript_parts)
    actual_cost = config.calculate_transcribe_cost(total_duration)

    result = {
        "transcript": unified_transcript,
        "language": norm_lang,
        "duration_seconds": total_duration,
        "chunks_processed": len(chunks),
        "model": f"indic-transcribe-{model_variant}",
        "cost_inr": actual_cost,
        "cached": False,
    }

    # Store in cache
    cache.set(cache_key, result)
    return result
