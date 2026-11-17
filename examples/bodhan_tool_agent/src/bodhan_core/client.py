"""Async HTTP client for the four Bodhan AI model endpoints."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import pathlib
from typing import Any

import httpx

from .cache import ToolCache
from .cost import (
    CostMeter,
    estimate_ocr,
    estimate_speak,
    estimate_transcribe,
    estimate_translate,
)
from .errors import BodhanError, ErrorCode
from .retry import RetryError, with_retry

BASE_URL = "https://api.bodhan.ai"


class BodhanClient:
    """Async client wrapping all four Bodhan AI model endpoints.

    Args:
        cache: :class:`~bodhan_core.cache.ToolCache` for caching responses.
        cost_meter: :class:`~bodhan_core.cost.CostMeter` for spend tracking.
        max_budget_inr: Hard budget cap in INR; calls that would exceed it
            raise :class:`~bodhan_core.errors.BodhanError` ``BUDGET_EXCEEDED``.
        semaphore_limit: Maximum number of concurrent in-flight HTTP requests.
    """

    def __init__(
        self,
        cache: ToolCache,
        cost_meter: CostMeter,
        max_budget_inr: float = 20.0,
        semaphore_limit: int = 4,
    ) -> None:
        self._keys: dict[str, str] = {
            "transcribe": os.environ.get("BODHAN_TRANSCRIBE_KEY") or os.environ.get("BODHAN_API_KEY", ""),
            "translate": os.environ.get("BODHAN_TRANSLATE_KEY") or os.environ.get("BODHAN_API_KEY", ""),
            "speak": os.environ.get("BODHAN_SPEAK_KEY") or os.environ.get("BODHAN_API_KEY", ""),
            "ocr": os.environ.get("BODHAN_OCR_KEY") or os.environ.get("BODHAN_API_KEY", ""),
        }
        self._sem = asyncio.Semaphore(semaphore_limit)
        self.cache = cache
        self.cost_meter = cost_meter
        self.max_budget_inr = max_budget_inr

    def _get_key(self, model: str) -> str:
        key = self._keys.get(model, "")
        if not key:
            env_var = f"BODHAN_{model.upper()}_KEY"
            raise BodhanError(
                code=ErrorCode.INVALID_ARGUMENT,
                message=f"Missing API key for {model}. Environment variable '{env_var}' is not configured.",
                how_to_fix=f"Obtain a key from https://console.bodhan.ai/ and set {env_var} in your environment or .env file.",
                retryable=False,
            )
        return key

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _remaining_budget(self) -> float:
        return self.max_budget_inr - self.cost_meter.total_inr

    def _check_budget(self, estimated: float) -> None:
        if not self.cost_meter.check_budget(estimated, self._remaining_budget()):
            raise BodhanError(
                code=ErrorCode.BUDGET_EXCEEDED,
                message=(
                    f"Estimated cost ₹{estimated:.4f} would exceed remaining "
                    f"budget ₹{self._remaining_budget():.4f}."
                ),
                how_to_fix="Increase max_budget_inr or reduce the input size.",
                retryable=False,
            )

    @staticmethod
    def _bytes_key(*parts: bytes | str) -> str:
        h = hashlib.sha256()
        for part in parts:
            h.update(part if isinstance(part, bytes) else part.encode())
        return h.hexdigest()

    # ------------------------------------------------------------------
    # transcribe
    # ------------------------------------------------------------------

    async def transcribe(
        self,
        audio_path: pathlib.Path,
        language: str | None = None,
        response_format: str = "text",
    ) -> dict[str, Any]:
        """Transcribe an audio file.

        POST /v1/audio/transcriptions (multipart/form-data)
        Returns ``{text: str}`` for plain text; raw string for srt/vtt.
        """
        audio_bytes = audio_path.read_bytes()

        # Cache key
        lang_part = (language or "").encode()
        fmt_part = response_format.encode()
        cache_key = self._bytes_key(audio_bytes, lang_part, fmt_part)

        cached = self.cache.get(cache_key)
        if cached is not None:
            result = json.loads(cached)
            self.cost_meter.add("transcribe", 0.0, cached=True)
            return result

        # Budget estimation — we don't know duration yet; use a 1-min floor
        # The caller (tool layer) should probe duration and pass it in kwargs;
        # here we fall back to file-size heuristic (128kbps ≈ 16kB/s).
        heuristic_secs = max(60.0, len(audio_bytes) / 16_000)
        estimated = estimate_transcribe(heuristic_secs)
        self._check_budget(estimated)

        key = self._get_key("transcribe")
        url = f"{BASE_URL}/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {key}"}

        async def _do() -> dict[str, Any]:
            async with self._sem:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    files: dict[str, Any] = {
                        "file": (audio_path.name, audio_bytes, "audio/wav"),
                        "model": (None, "indic-transcribe"),
                        "response_format": (None, response_format),
                    }
                    if language:
                        files["language"] = (None, language)
                    r = client.build_request("POST", url, headers=headers, files=files)
                    resp = await client.send(r)
                    resp.raise_for_status()
                    if response_format == "text":
                        return resp.json()
                    return {"text": resp.text}

        try:
            result = await with_retry(_do)
        except RetryError as exc:
            raise BodhanError(
                ErrorCode.UPSTREAM_ERROR,
                f"Transcription failed after retries: {exc}",
                "Check your BODHAN_TRANSCRIBE_KEY and network connectivity.",
                retryable=True,
            ) from exc

        self.cache.put(cache_key, json.dumps(result).encode())
        self.cost_meter.add("transcribe", estimated, cached=False)
        return result

    # ------------------------------------------------------------------
    # translate
    # ------------------------------------------------------------------

    async def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str | None = None,
    ) -> dict[str, Any]:
        """Translate *text* to *target_lang*.

        POST /translate (application/json)
        Returns ``{translated_text, source_lang, tokens}``.
        """
        # Rough token estimate: 1 token ≈ 4 chars
        estimated_tokens = max(1, len(text) // 4)
        estimated_cost = estimate_translate(estimated_tokens)
        self._check_budget(estimated_cost)

        cache_key = self._bytes_key(
            text.encode(),
            target_lang.encode(),
            (source_lang or "").encode(),
        )

        cached = self.cache.get(cache_key)
        if cached is not None:
            result = json.loads(cached)
            self.cost_meter.add("translate", 0.0, cached=True)
            return result

        key = self._get_key("translate")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {"text": text, "target_language": target_lang}
        if source_lang:
            body["source_language"] = source_lang

        async def _do() -> dict[str, Any]:
            async with self._sem:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.post(f"{BASE_URL}/translate", json=body, headers=headers)
                    if resp.status_code == 404:
                        # Fallback to OpenAI-compatible chat completions transport
                        chat_body = {
                            "model": "indic-translate",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"Translate into {target_lang}:\n{text}",
                                }
                            ],
                        }
                        chat_resp = await client.post(
                            f"{BASE_URL}/v1/chat/completions",
                            json=chat_body,
                            headers=headers,
                        )
                        chat_resp.raise_for_status()
                        data = chat_resp.json()
                        content = data["choices"][0]["message"]["content"]
                        usage = data.get("usage", {})
                        return {
                            "translation": content,
                            "target_language": target_lang,
                            "script": "native",
                            "tokens": usage.get("completion_tokens", estimated_tokens),
                        }
                    resp.raise_for_status()
                    return resp.json()

        try:
            result = await with_retry(_do)
        except RetryError as exc:
            raise BodhanError(
                ErrorCode.UPSTREAM_ERROR,
                f"Translation failed after retries: {exc}",
                "Check your BODHAN_TRANSLATE_KEY and network connectivity.",
                retryable=True,
            ) from exc

        # Use actual token count if the API returns it
        actual_tokens = result.get("tokens", estimated_tokens)
        actual_cost = estimate_translate(actual_tokens)

        self.cache.put(cache_key, json.dumps(result).encode())
        self.cost_meter.add("translate", actual_cost, cached=False)
        return result

    # ------------------------------------------------------------------
    # speak
    # ------------------------------------------------------------------

    async def speak(
        self,
        text: str,
        language: str,
        voice: str,
        style: str | None = None,
    ) -> bytes:
        """Synthesise *text* to speech.

        POST /v1/audio/speech (application/json)
        Returns raw WAV bytes.
        """
        estimated_cost = estimate_speak(len(text))
        self._check_budget(estimated_cost)

        cache_key = self._bytes_key(
            text.encode(),
            language.encode(),
            voice.encode(),
            (style or "").encode(),
        )

        cached = self.cache.get(cache_key)
        if cached is not None:
            self.cost_meter.add("speak", 0.0, cached=True)
            return cached

        key = self._get_key("speak")
        url = f"{BASE_URL}/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        instructions = json.dumps({"lang": language, "style": style} if style else {"lang": language})
        body: dict[str, Any] = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": instructions,
        }

        async def _do() -> bytes:
            async with self._sem:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    return resp.content

        try:
            audio_bytes = await with_retry(_do)
        except RetryError as exc:
            raise BodhanError(
                ErrorCode.UPSTREAM_ERROR,
                f"Speech synthesis failed after retries: {exc}",
                "Check your BODHAN_SPEAK_KEY and network connectivity.",
                retryable=True,
            ) from exc

        self.cache.put(cache_key, audio_bytes)
        self.cost_meter.add("speak", estimated_cost, cached=False)
        return audio_bytes

    # ------------------------------------------------------------------
    # ocr
    # ------------------------------------------------------------------

    async def ocr(
        self,
        image_path: pathlib.Path,
        table_format: str = "markdown",
    ) -> dict[str, Any]:
        """Extract text from *image_path* using the Indic-OCR model.

        POST /v1/chat/completions with the image as a base64 data URL.
        Returns ``{markdown: str, blocks: list}``.
        """
        estimated_cost = estimate_ocr(1)
        self._check_budget(estimated_cost)

        image_bytes = image_path.read_bytes()
        cache_key = self._bytes_key(image_bytes, table_format.encode())

        cached = self.cache.get(cache_key)
        if cached is not None:
            result = json.loads(cached)
            self.cost_meter.add("ocr", 0.0, cached=True)
            return result

        # Build base64 data URL
        suffix = image_path.suffix.lower()
        mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"

        key = self._get_key("ocr")
        url = f"{BASE_URL}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        system_prompt = (
            "You are an Indic-OCR engine. Extract all text from the image. "
            f"Format tables as {table_format}. Return JSON: "
            '{"markdown": "<full text>", "blocks": [{"type": "text|table|heading", "content": "..."}]}'
        )
        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": system_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }

        async def _do() -> dict[str, Any]:
            async with self._sem:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    raw = resp.json()
                    # Extract the assistant message content
                    content = raw["choices"][0]["message"]["content"]
                    if isinstance(content, str):
                        # Try to parse as JSON
                        try:
                            return json.loads(content)
                        except json.JSONDecodeError:
                            return {"markdown": content, "blocks": []}
                    return content

        try:
            result = await with_retry(_do)
        except RetryError as exc:
            raise BodhanError(
                ErrorCode.UPSTREAM_ERROR,
                f"OCR failed after retries: {exc}",
                "Check your BODHAN_OCR_KEY and network connectivity.",
                retryable=True,
            ) from exc

        self.cache.put(cache_key, json.dumps(result).encode())
        self.cost_meter.add("ocr", estimated_cost, cached=False)
        return result
