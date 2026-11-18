"""Translation tool wrapping Indic-Translate."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from bodhan_mcp.clients.translate import TranslateClient
from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set
from bodhan_mcp.core.chunking import chunk_text, count_tokens
from bodhan_mcp.core.cost import calculate_translate_cost
from bodhan_mcp.core.errors import BodhanError, FILE_NOT_FOUND, INVALID_INPUT, UNSUPPORTED_LANGUAGE
from bodhan_mcp.core.file_store import default_file_store
from bodhan_mcp.schemas import TranslateTextOutput

logger = logging.getLogger(__name__)


async def translate_text(
    text: Optional[str] = None,
    file_path: Optional[str] = None,
    target_language: str = "en",
    source_language: Optional[str] = None,
) -> TranslateTextOutput:
    """Translate text between Indic languages and English using Indic-Translate.
    
    Handles arbitrarily long texts by chunking at paragraph/token boundaries.
    Preserves Markdown and formatting.
    """
    t0 = time.time()

    if not text and not file_path:
        raise BodhanError(INVALID_INPUT, "Either 'text' or 'file_path' must be provided.")

    if file_path:
        p = Path(file_path)
        if not p.exists():
            raise BodhanError(FILE_NOT_FOUND, f"File not found: {file_path}")
        try:
            content = p.read_text(encoding="utf-8")
        except Exception as exc:
            raise BodhanError(INVALID_INPUT, f"Failed to read file: {exc}")
    else:
        content = text or ""

    tgt = str(target_language).lower()
    if tgt not in settings.translate_languages:
        raise BodhanError(
            UNSUPPORTED_LANGUAGE,
            f"Target language '{target_language}' is not supported by Indic-Translate. "
            f"Supported: {sorted(list(settings.translate_languages))}",
            retryable=False,
            hint="Indic-Translate supports 18 Indic languages plus English ('en') as target.",
        )

    if source_language:
        src = str(source_language).lower()
        if src not in settings.translate_languages:
            raise BodhanError(
                UNSUPPORTED_LANGUAGE,
                f"Source language '{source_language}' is not supported.",
                retryable=False,
            )
    else:
        src = None

    # Cache check
    cache_params = {"tgt": tgt, "src": src}
    cached_val = cache_get("indic-translate", cache_params, content)
    if cached_val:
        logger.info("Cache hit for translation (target=%s)", tgt)
        cached_val["cached"] = True
        cached_val["latency_seconds"] = round(time.time() - t0, 3)
        cached_val["cost_inr"] = 0.0
        return TranslateTextOutput(**cached_val)

    client = TranslateClient()
    chunks = chunk_text(content, max_tokens=settings.translate_max_tokens)

    translated_chunks = []
    total_tokens = 0

    for chunk in chunks:
        res = await client.translate_text(
            chunk,
            target_language_code=tgt,
            target_script="native",
            source_language_code=src,
        )
        translated_chunks.append(res["translated_text"])
        total_tokens += res.get("output_tokens", count_tokens(res["translated_text"]))

    full_translated = "\n\n".join(translated_chunks)
    cost = calculate_translate_cost(total_tokens)
    latency = round(time.time() - t0, 3)

    out_file = None
    if file_path or len(full_translated) > 1000:
        out_file = default_file_store.save_text(full_translated, extension=".md", prefix="trans")

    output = TranslateTextOutput(
        ok=True,
        translated_text=full_translated,
        output_file_path=out_file,
        source_language_detected=src,
        target_language=tgt,
        chunks_processed=len(chunks),
        output_tokens=total_tokens,
        cost_inr=cost,
        latency_seconds=latency,
        cached=False,
    )

    cache_set("indic-translate", cache_params, content, output.model_dump())
    return output
