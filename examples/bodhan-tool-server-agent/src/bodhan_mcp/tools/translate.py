"""Indic-Translate Tool for Bodhan MCP.

Translates text across 22 scheduled Indian languages and English.
Preserves Markdown, LaTeX, code, and table structure while handling context chunking.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from bodhan_mcp.cache import cache
from bodhan_mcp.clients.bodhan_client import bodhan_client
from bodhan_mcp.config import config, is_supported_language, normalize_language_code
from bodhan_mcp.media_utils import validate_file_reference

logger = logging.getLogger("bodhan.tools.translate")


def split_text_into_chunks(text: str, max_chars: int = 4000) -> List[str]:
    """Split long text along paragraph or line boundaries to preserve document structure."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_length = 0

    for para in paragraphs:
        para_len = len(para) + 2
        if current_length + para_len > max_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_length = para_len
        else:
            current_chunk.append(para)
            current_length += para_len

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


async def translate_text(
    text: str = "",
    text_path: Optional[Union[str, Path]] = None,
    source_lang: str = "en",
    target_lang: str = "hi",
) -> Dict[str, Any]:
    """Translate text into an Indian language using Bodhan Indic-Translate.
    
    Args:
        text: Raw text string to translate (if text_path not provided).
        text_path: Path to text file to translate (preferred for large inputs).
        source_lang: Source language name or code (default: 'en').
        target_lang: Target language name or code (e.g. 'hi', 'kn', 'ta', 'te').
        
    Returns:
        Structured dictionary with translated text, token count, and INR cost.
    """
    if text_path:
        p = validate_file_reference(text_path, must_exist=True)
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = text or ""

    if not content.strip():
        raise ValueError("Cannot translate empty text.")

    # Validate languages
    src_norm = normalize_language_code(source_lang or "en") or "en"
    tgt_norm = normalize_language_code(target_lang)
    if not tgt_norm:
        raise ValueError(
            f"Unsupported target language '{target_lang}' for Indic-Translate. "
            f"Bodhan only supports the 22 scheduled Indian languages and English."
        )

    # Check cache
    params = {"tool": "translate", "source": src_norm, "target": tgt_norm}
    cache_key = cache.generate_key("translate", params, content)
    cached_result = cache.get(cache_key)
    if cached_result:
        cached_result["cached"] = True
        cached_result["cost_inr"] = 0.0
        return cached_result

    # Chunk text if necessary to preserve structure
    text_chunks = split_text_into_chunks(content, max_chars=config.max_translate_chunk_chars)
    translated_parts = []
    total_tokens = 0

    for chunk in text_chunks:
        res = await bodhan_client.translate(
            text=chunk,
            target_lang=tgt_norm,
            source_lang=src_norm,
        )
        translated_parts.append(res.get("translated_text", ""))
        tokens = res.get("usage", {}).get("completion_tokens", max(1, len(chunk.split()) * 2))
        total_tokens += tokens

    final_translated_text = "\n\n".join(translated_parts)
    actual_cost = config.calculate_translate_cost(total_tokens)

    result = {
        "translated_text": final_translated_text,
        "source_language": src_norm,
        "target_language": tgt_norm,
        "token_count": total_tokens,
        "model": "indic-translate",
        "cost_inr": actual_cost,
        "cached": False,
    }

    cache.set(cache_key, result)
    return result
