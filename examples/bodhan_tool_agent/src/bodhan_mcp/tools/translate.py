"""
Translate text using Indic-Translate.

Handles long documents by splitting at paragraph boundaries so each chunk
fits well within the 32K-token context window. Chunks are translated
concurrently and re-joined in order.
"""
from __future__ import annotations

import asyncio
import re

from ..clients.bodhan_client import call_translate

_MAX_CHARS_PER_CHUNK = 4_000  # ~1K tokens; well under the 32K limit


def _split_at_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split text at double-newline boundaries, respecting max_chars per chunk."""
    paragraphs = re.split(r"\n\n+", text)
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if not para.strip():
            continue
        if not current:
            current = para
        elif len(current) + 2 + len(para) <= max_chars:
            current += "\n\n" + para
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks or [text]


async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
) -> dict:
    """Translate text from source_lang to target_lang.

    Args:
        text:        Source text. May be any length; long text is split internally.
        source_lang: BCP-47 source language code (e.g. "en", "hi").
        target_lang: BCP-47 target language code.

    Returns:
        {
          "translated_text": str,
          "source_lang": str,
          "target_lang": str,
          "cost_inr": float,
        }
    """
    if source_lang == target_lang:
        return {
            "translated_text": text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "cost_inr": 0.0,
        }

    chunks = _split_at_paragraphs(text, _MAX_CHARS_PER_CHUNK)
    results = await asyncio.gather(
        *[call_translate(chunk, source_lang, target_lang) for chunk in chunks]
    )

    translated_parts = [t for t, _ in results]
    total_cost = sum(c for _, c in results)

    return {
        "translated_text": "\n\n".join(translated_parts),
        "source_lang": source_lang,
        "target_lang": target_lang,
        "cost_inr": round(total_cost, 4),
    }
