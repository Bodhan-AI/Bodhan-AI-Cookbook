"""
Translate tool — text translation using Bodhan Indic-Translate.

Handles:
- Language pair validation (supported languages check)
- Long text chunking (respects 32K token context window)
- Markdown/table structure preservation
- Same-language no-op
"""

from __future__ import annotations

import logging
import re
from typing import Any

from bodhan_mcp.client import (
    BodhanClient,
    BodhanAPIError,
    UnsupportedLanguageError,
    SUPPORTED_LANGUAGE_CODES,
)

logger = logging.getLogger(__name__)

# Rough estimate: 1 token ≈ 4 characters for English, ~2.5 for Indic scripts
CHARS_PER_TOKEN_EN = 4
CHARS_PER_TOKEN_INDIC = 2.5
MAX_CONTEXT_TOKENS = 30_000  # Leave 2K buffer from 32K limit


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate."""
    # Count Indic characters vs Latin
    indic_chars = sum(1 for c in text if ord(c) > 0x0900)
    latin_chars = len(text) - indic_chars
    return int(latin_chars / CHARS_PER_TOKEN_EN + indic_chars / CHARS_PER_TOKEN_INDIC)


def _chunk_text(text: str, max_tokens: int = MAX_CONTEXT_TOKENS) -> list[str]:
    """
    Split text into chunks that fit within the token limit.

    Tries to split on paragraph boundaries first, then sentences.
    """
    est_tokens = _estimate_tokens(text)
    if est_tokens <= max_tokens:
        return [text]

    # Split on double newlines (paragraphs)
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        if para_tokens > max_tokens:
            # Paragraph too long — split on sentences
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            sentences = re.split(r"(?<=[.!?।])\s+", para)
            for sent in sentences:
                if _estimate_tokens(current_chunk + " " + sent) > max_tokens:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sent
                else:
                    current_chunk = (current_chunk + " " + sent).strip()
        elif _estimate_tokens(current_chunk + "\n\n" + para) > max_tokens:
            chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            current_chunk = (current_chunk + "\n\n" + para).strip() if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


async def translate_text(
    client: BodhanClient,
    text: str,
    source_language: str,
    target_language: str,
) -> dict[str, Any]:
    """
    Translate text from one language to another.

    Automatically chunks long text to stay within the 32K token context window.
    Preserves markdown structure, tables, and formatting.

    Args:
        client: BodhanClient instance.
        text: The text to translate.
        source_language: ISO 639-1 code of the source language (e.g., 'en', 'hi').
        target_language: ISO 639-1 code of the target language (e.g., 'ta', 'mr').

    Returns:
        dict with:
            - translated_text: The full translated text
            - source_language: Source language code
            - target_language: Target language code
            - num_chunks: Number of chunks translated
            - char_count: Total character count of source text

    Raises:
        UnsupportedLanguageError: If either language is not supported.
        BodhanAPIError: If the API call fails.
    """
    if not text.strip():
        return {
            "translated_text": "",
            "source_language": source_language,
            "target_language": target_language,
            "num_chunks": 0,
            "char_count": 0,
            "note": "Empty input text; nothing to translate.",
        }

    # Validate languages
    for lang, label in [(source_language, "source"), (target_language, "target")]:
        if lang not in SUPPORTED_LANGUAGE_CODES:
            raise UnsupportedLanguageError(
                f"Unsupported {label} language: '{lang}'. "
                f"Bodhan Indic-Translate supports: English and 22 scheduled Indian languages. "
                f"Notably, French, Spanish, German, Chinese, Japanese, Korean, Arabic (non-Urdu), "
                f"and other non-Indian languages are NOT supported.",
                model="indic-translate",
            )

    # Same language — no-op
    if source_language == target_language:
        return {
            "translated_text": text,
            "source_language": source_language,
            "target_language": target_language,
            "num_chunks": 0,
            "char_count": len(text),
            "note": "Source and target languages are the same. Returning original text.",
        }

    # Chunk if needed
    chunks = _chunk_text(text)
    logger.info(f"Translating {len(text)} chars in {len(chunks)} chunk(s): {source_language} → {target_language}")

    translated_parts = []
    for i, chunk in enumerate(chunks):
        result = await client.translate(
            text=chunk,
            source_language=source_language,
            target_language=target_language,
        )
        translated_parts.append(result["translated_text"])
        logger.debug(f"Chunk {i+1}/{len(chunks)} translated")

    translated_text = "\n\n".join(translated_parts)

    return {
        "translated_text": translated_text,
        "source_language": source_language,
        "target_language": target_language,
        "num_chunks": len(chunks),
        "char_count": len(text),
    }


def get_supported_languages() -> dict[str, str]:
    """Return a dict of supported language codes and their names."""
    from bodhan_mcp.helpers.detect_language import LANGUAGE_NAMES
    return {
        code: LANGUAGE_NAMES.get(code, code)
        for code in sorted(SUPPORTED_LANGUAGE_CODES)
    }
