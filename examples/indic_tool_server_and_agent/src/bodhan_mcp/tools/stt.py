"""Speech-to-Text tool wrapping Indic-Transcribe."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, List

from bodhan_mcp.clients.transcribe import TranscribeClient
from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set, file_content_hash
from bodhan_mcp.core.chunking import chunk_audio, get_audio_duration_seconds
from bodhan_mcp.core.cost import calculate_stt_cost
from bodhan_mcp.core.errors import BodhanError, FILE_NOT_FOUND, INVALID_INPUT, UNSUPPORTED_LANGUAGE
from bodhan_mcp.schemas import (
    TranscribeAudioInput,
    TranscribeAudioOutput,
    TranscriptSegment,
    STTLanguage,
)

logger = logging.getLogger(__name__)


async def transcribe_audio(
    file_path: str,
    language: Optional[str] = None,
    vad: bool = True,
) -> TranscribeAudioOutput:
    """Transcribe audio file in an Indic language using Indic-Transcribe.
    
    Handles audio of any length by automatically slicing into <= 30-second segments.
    """
    t0 = time.time()
    p = Path(file_path)
    if not p.exists():
        raise BodhanError(FILE_NOT_FOUND, f"Audio file not found: {file_path}", hint="Check file path.")

    # Validate language if supplied
    if language:
        lang_str = str(language).lower()
        if lang_str not in settings.stt_languages:
            raise BodhanError(
                UNSUPPORTED_LANGUAGE,
                f"Language '{language}' is not supported by Indic-Transcribe. "
                f"Supported: {sorted(list(settings.stt_languages))}",
                retryable=False,
            )
        lang_val = lang_str
    else:
        lang_val = None

    duration = get_audio_duration_seconds(file_path)

    # Check cache
    file_hash = file_content_hash(file_path)
    cache_params = {"lang": lang_val, "vad": vad, "duration": duration}
    cached_val = cache_get("indic-transcribe", cache_params, file_hash)
    if cached_val:
        logger.info("Cache hit for audio %s", p.name)
        cached_val["cached"] = True
        cached_val["latency_seconds"] = round(time.time() - t0, 3)
        cached_val["cost_inr"] = 0.0
        return TranscribeAudioOutput(**cached_val)

    client = TranscribeClient()
    chunks = chunk_audio(file_path, chunk_seconds=settings.stt_max_chunk_seconds)

    segments: List[TranscriptSegment] = []
    text_parts: List[str] = []
    detected_lang = lang_val

    for chunk_file, start_sec, end_sec in chunks:
        res = await client.transcribe_chunk(chunk_file, language=lang_val, vad=vad)
        txt = res.get("text", "").strip()
        if "language" in res and not detected_lang:
            detected_lang = res["language"]
        if txt:
            text_parts.append(txt)
            segments.append(
                TranscriptSegment(
                    start_seconds=round(start_sec, 2),
                    end_seconds=round(end_sec, 2),
                    text=txt,
                )
            )

    full_text = " ".join(text_parts)
    cost = calculate_stt_cost(duration)
    latency = round(time.time() - t0, 3)

    output = TranscribeAudioOutput(
        ok=True,
        file_path=str(p.resolve()),
        detected_language=detected_lang,
        full_text=full_text,
        segments=segments,
        duration_seconds=round(duration, 2),
        chunks_processed=len(chunks),
        cost_inr=cost,
        latency_seconds=latency,
        cached=False,
    )

    cache_set("indic-transcribe", cache_params, file_hash, output.model_dump())
    return output
