"""Async client for Indic-Translate."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import httpx

from bodhan_mcp.config import settings
from bodhan_mcp.core.cache import cache_get, cache_set
from bodhan_mcp.core.chunking import chunk_text, count_tokens
from bodhan_mcp.core.cost import calculate_translate_cost
from bodhan_mcp.core.errors import (
    BodhanError,
    INVALID_INPUT,
    RATE_LIMITED,
    TIMEOUT,
    UPSTREAM_ERROR,
)
from bodhan_mcp.core.retry import bodhan_retry

logger = logging.getLogger(__name__)


@dataclass
class TranslateResult:
    translated_text: str
    output_tokens: int = 0
    cost: float = 0.0
    cached: bool = False


class TranslateClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or settings.translate_key
        self.base_url = (base_url or settings.bodhan_base_url).rstrip("/")
        self.endpoint = f"{self.base_url}/chat/completions"

    @bodhan_retry
    async def translate_text(
        self,
        text: str,
        target_language_code: str,
        target_script: str = "native",
        source_language_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Translate a single block of text (<= 32K tokens) using Indic-Translate."""
        if not text or not text.strip():
            return {"translated_text": "", "output_tokens": 0}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target_language_code,
            "target_script": target_script,
        }
        if source_language_code:
            payload["source_language_code"] = source_language_code

        logger.info("Calling Indic-Translate (target=%s, chars=%d)", target_language_code, len(text))

        try:
            async with httpx.AsyncClient(timeout=settings.api_timeout_seconds) as client:
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                )

            if resp.status_code == 200:
                data = resp.json()
                translated = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                output_tokens = usage.get("completion_tokens", len(translated.split()))
                return {
                    "translated_text": translated,
                    "output_tokens": output_tokens,
                }
            elif resp.status_code == 429:
                raise BodhanError(RATE_LIMITED, "Translate API rate limit exceeded", retryable=True)
            elif resp.status_code in (408, 504):
                raise BodhanError(TIMEOUT, f"Translate API timed out: {resp.text}", retryable=True)
            elif resp.status_code >= 500:
                raise BodhanError(UPSTREAM_ERROR, f"Translate API server error ({resp.status_code}): {resp.text}", retryable=True)
            else:
                raise BodhanError(INVALID_INPUT, f"Translate API error ({resp.status_code}): {resp.text}", retryable=False)

        except httpx.TimeoutException as exc:
            raise BodhanError(TIMEOUT, f"Translate request timed out: {exc}", retryable=True)
        except httpx.RequestError as exc:
            raise BodhanError(UPSTREAM_ERROR, f"Translate network request error: {exc}", retryable=True)

    async def translate(
        self,
        text: str,
        target_lang: str,
        target_script: str = "native",
        source_lang: Optional[str] = None,
    ) -> TranslateResult:
        """High-level translate method handling chunking, caching, and cost."""
        if not text or not text.strip():
            return TranslateResult(translated_text="", output_tokens=0, cost=0.0)

        cache_params = {"tgt": target_lang, "src": source_lang, "script": target_script}
        cached = cache_get("indic-translate", cache_params, text)
        if cached:
            return TranslateResult(
                translated_text=cached["translated_text"],
                output_tokens=cached.get("output_tokens", 0),
                cost=0.0,
                cached=True,
            )

        chunks = chunk_text(text, max_tokens=settings.translate_max_tokens)
        translated_chunks = []
        total_tokens = 0

        for chunk in chunks:
            res = await self.translate_text(
                chunk,
                target_language_code=target_lang,
                target_script=target_script,
                source_language_code=source_lang,
            )
            translated_chunks.append(res["translated_text"])
            total_tokens += res.get("output_tokens", count_tokens(res["translated_text"]))

        full_text = "\n\n".join(translated_chunks)
        cost = calculate_translate_cost(total_tokens)

        cache_set(
            "indic-translate",
            cache_params,
            text,
            {"translated_text": full_text, "output_tokens": total_tokens},
        )

        return TranslateResult(
            translated_text=full_text,
            output_tokens=total_tokens,
            cost=cost,
            cached=False,
        )
