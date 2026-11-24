"""Bodhan Translation tool using indic-translate."""

from __future__ import annotations

import os
import time
import httpx

from bodhan_mcp.config import (
    BODHAN_API_KEY,
    BODHAN_BASE_URL,
    BODHAN_TRANSLATE_API_KEY,
    MAX_TEXT_CHUNK_CHARS,
    PRICING,
    TRANSLATE_LANGUAGES,
    TRANSLATE_SCRIPTS,
)
from bodhan_mcp.errors import (
    BodhanAPIError,
    BodhanAuthenticationError,
    BodhanRateLimitError,
    InvalidInputError,
    UnsupportedLanguageError,
)
from bodhan_mcp.schemas import TranslateTextOutput


def _chunk_text_preserving_structure(text: str, max_chars: int = MAX_TEXT_CHUNK_CHARS) -> list[str]:
    """Split text along paragraph or sentence boundaries so formatting is preserved."""
    if len(text) <= max_chars:
        return [text]

    # Split by double newlines first (paragraphs)
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for p in paragraphs:
        if current_len + len(p) + 2 <= max_chars:
            current_chunk.append(p)
            current_len += len(p) + 2
        else:
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0

            # If a single paragraph is too large, split by single newline or sentence terminators
            if len(p) > max_chars:
                lines = p.split("\n")
                line_chunk: list[str] = []
                line_len = 0
                for line in lines:
                    if line_len + len(line) + 1 <= max_chars:
                        line_chunk.append(line)
                        line_len += len(line) + 1
                    else:
                        if line_chunk:
                            chunks.append("\n".join(line_chunk))
                            line_chunk = []
                            line_len = 0
                        chunks.append(line)
                if line_chunk:
                    chunks.append("\n".join(line_chunk))
            else:
                current_chunk.append(p)
                current_len = len(p)

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return [c for c in chunks if c.strip()]


def _call_translate_api(
    client: httpx.Client,
    text: str,
    target_language: str,
    script: str,
    api_key: str,
    base_url: str,
    max_retries: int = 3,
) -> dict:
    """Call POST /translate with retry and backoff."""
    url = f"{base_url}/translate"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "target_language": target_language,
        "script": script,
    }

    backoff = 1.0
    for attempt in range(max_retries):
        try:
            resp = client.post(url, headers=headers, json=body, timeout=60.0)
            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 404:
                # Fallback to OpenAI-compatible /v1/chat/completions endpoint for indic-translate
                chat_url = f"{base_url}/v1/chat/completions"
                chat_body = {
                    "model": "indic-translate",
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Translate the following text into {target_language} ({script} script). "
                                f"Maintain the markdown structure and formatting:\n\n{text}"
                            ),
                        }
                    ],
                }
                chat_resp = client.post(chat_url, headers=headers, json=chat_body, timeout=60.0)
                if chat_resp.status_code == 200:
                    data = chat_resp.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    return {"translation": content}
                if chat_resp.status_code == 401:
                    raise BodhanAuthenticationError("Invalid or missing BODHAN_API_KEY")

            if resp.status_code == 401:
                raise BodhanAuthenticationError("Invalid or missing BODHAN_API_KEY")

            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanRateLimitError("Rate limit exceeded for translation")

            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise BodhanAPIError(resp.status_code, f"Server error from Bodhan API: {resp.text}")

            raise BodhanAPIError(resp.status_code, f"Translation API failed: {resp.text}")

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise BodhanAPIError(504, f"Network timeout during translation: {e}")

    return {"translation": ""}


def translate_text(
    text: str,
    target_language: str,
    script: str = "native",
    preserve_formatting: bool = True,
    api_key: str | None = None,
    base_url: str | None = None,
) -> TranslateTextOutput:
    """Translate text into an Indian language or English using Bodhan indic-translate.

    Validates language support, splits long documents intelligently, preserves Markdown structure,
    and returns translated content without silent truncation.
    """
    key = api_key or os.getenv("BODHAN_TRANSLATE_API_KEY") or os.getenv("BODHAN_API_KEY") or BODHAN_TRANSLATE_API_KEY or BODHAN_API_KEY
    if not key:
        raise BodhanAuthenticationError("BODHAN_API_KEY is not set")
    api_root = (base_url or os.getenv("BODHAN_BASE_URL") or BODHAN_BASE_URL).rstrip("/")

    if not text or not text.strip():
        raise InvalidInputError("Input text to translate cannot be empty", field="text")

    target_clean = target_language.strip()
    if target_clean not in TRANSLATE_LANGUAGES:
        raise UnsupportedLanguageError(target_clean, "translation", list(TRANSLATE_LANGUAGES))

    script_clean = script.strip().lower()
    if script_clean not in TRANSLATE_SCRIPTS:
        raise InvalidInputError(
            f"Invalid script '{script}'. Allowed scripts: {', '.join(TRANSLATE_SCRIPTS)}",
            field="script",
        )

    chunks = _chunk_text_preserving_structure(text) if preserve_formatting else [text]
    translated_parts = []
    total_out_chars = 0
    was_truncated = False

    with httpx.Client() as client:
        for chunk in chunks:
            res = _call_translate_api(
                client=client,
                text=chunk,
                target_language=target_clean,
                script=script_clean,
                api_key=key,
                base_url=api_root,
            )
            part_translation = res.get("translation", "")
            if res.get("truncated"):
                was_truncated = True
            translated_parts.append(part_translation)
            total_out_chars += len(part_translation)

    full_translation = "\n\n".join(translated_parts) if preserve_formatting else "".join(translated_parts)

    # Estimate cost: approx 4 characters per token in output
    estimated_tokens = max(1, total_out_chars // 4)
    cost = round((estimated_tokens / 1000.0) * PRICING.translate_per_1k_out_tokens_inr, 4)

    return TranslateTextOutput(
        success=True,
        translation=full_translation,
        target_language=target_clean,
        script=script_clean,
        truncated=was_truncated,
        source_char_count=len(text),
        output_char_count=total_out_chars,
        estimated_cost_inr=cost,
    )
