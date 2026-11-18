#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp

from .models import (
    BODHAN_BASE_URL,
    COST_PER_MINUTE_STT,
    COST_PER_10K_CHARS_SPEAK,
    COST_PER_10K_OUTPUT_TOKENS_TRANSLATE,
    COST_PER_IMAGE_OCR,
    ErrorCode,
)


INDR_TRANSCRIBE_KEY = os.environ.get("BODHAN_TRANSCRIBE_KEY", "")
INDR_TRANSLATE_KEY = os.environ.get("BODHAN_TRANSLATE_KEY", "")
INDR_SPEAK_KEY = os.environ.get("BODHAN_SPEAK_KEY", "")
INDR_OCR_KEY = os.environ.get("BODHAN_OCR_KEY", "")


@dataclass
class CachedResponse:
    response: Any
    cost_inr: float
    timestamp: float


@dataclass
class APIResponse:
    data: Any
    cost_inr: float
    cached: bool


class InrLedger:
    def __init__(self, max_cost: float = 20.0):
        self.total = 0.0
        self.max_cost = max_cost

    def add(self, cost: float) -> bool:
        if self.total + cost > self.max_cost:
            return False
        self.total += cost
        return True

    def remaining(self) -> float:
        return self.max_cost - self.total

    def is_exhausted(self) -> bool:
        return self.total >= self.max_cost

    def reset(self) -> None:
        self.total = 0.0


class BodhanCache:
    def __init__(self, cache_dir: str = ".cache/bodhan"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.semaphore = asyncio.Semaphore(3)

    def _key_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _hash(self, data: str | bytes) -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def _canonical_key(
        self, model: str, endpoint: str, params: dict, input_bytes: bytes
    ) -> str:
        canonical = {
            "model": model,
            "endpoint": endpoint,
            "params": params,
            "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
        }
        return self._hash(json.dumps(canonical, sort_keys=True))

    def get(
        self, model: str, endpoint: str, params: dict, input_bytes: bytes
    ) -> Optional[CachedResponse]:
        key = self._canonical_key(model, endpoint, params, input_bytes)
        path = self._key_path(key)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return CachedResponse(**data)
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def set(
        self,
        model: str,
        endpoint: str,
        params: dict,
        input_bytes: bytes,
        result: Any,
        cost_inr: float,
    ) -> None:
        key = self._canonical_key(model, endpoint, params, input_bytes)
        path = self._key_path(key)
        path.write_text(
            json.dumps(
                {"response": result, "cost_inr": cost_inr, "timestamp": time.time()}
            )
        )


class BodhanClient:
    def __init__(
        self,
        ledger: InrLedger | None = None,
        cache_dir: str = ".cache/bodhan",
        base_url: str = BODHAN_BASE_URL,
        default_timeout: int = 60,
        max_retries: int = 3,
    ):
        self.ledger = ledger or InrLedger()
        self.cache = BodhanCache(cache_dir)
        self.base_url = base_url
        self.default_timeout = default_timeout
        self.max_retries = max_retries

    def _auth_headers(self, model: str) -> dict[str, str]:
        api_key_map = {
            "indic-transcribe": INDR_TRANSCRIBE_KEY,
            "indic-translate": INDR_TRANSLATE_KEY,
            "indic-speak": INDR_SPEAK_KEY,
            "indic-ocr": INDR_OCR_KEY,
        }
        api_key = api_key_map.get(model, "")
        if not api_key:
            raise BodhanError(
                f"Missing API key for model {model}",
                code=ErrorCode.AUTHENTICATION_FAILED,
                retryable=False,
            )
        return {"Authorization": f"Bearer {api_key}"}

    def _calc_cost(
        self,
        model: str,
        json_data: Optional[dict],
        files: Optional[dict],
        result: Any,
    ) -> float:
        if model == "indic-transcribe":
            if result and isinstance(result, dict):
                duration = result.get("duration_sec", 0) or result.get("duration", 0)
                if duration:
                    return (duration / 60.0) * COST_PER_MINUTE_STT
            return COST_PER_MINUTE_STT
        elif model == "indic-translate":
            if result and isinstance(result, dict):
                usage = result.get("usage", {})
                output_tokens = usage.get("output_tokens", 0)
                if output_tokens:
                    return (
                        output_tokens / 10000.0
                    ) * COST_PER_10K_OUTPUT_TOKENS_TRANSLATE
            return 0.0
        elif model == "indic-speak":
            if json_data and "input" in json_data:
                input_chars = len(json_data["input"])
                return (input_chars / 10000.0) * COST_PER_10K_CHARS_SPEAK
            return 0.0
        elif model == "indic-ocr":
            return COST_PER_IMAGE_OCR
        return 0.0

    async def request(
        self,
        model: str,
        endpoint: str,
        json_data: Optional[dict] = None,
        files: Optional[dict] = None,
        timeout: int | None = None,
    ) -> APIResponse:
        timeout = timeout or self.default_timeout

        input_bytes = b""
        if files:
            for _, (_, fobj, _) in files.items():
                if hasattr(fobj, "read"):
                    pos = fobj.tell()
                    content = fobj.read()
                    fobj.seek(pos)
                    input_bytes += content
                else:
                    input_bytes += fobj
        elif json_data:
            input_bytes = json.dumps(json_data, sort_keys=True).encode("utf-8")

        cached = self.cache.get(model, endpoint, json_data or {}, input_bytes)
        if cached:
            return APIResponse(
                data=cached.response, cost_inr=cached.cost_inr, cached=True
            )

        if not self.ledger.add(0.0):
            raise BodhanError(
                "Budget exceeded before API call",
                code=ErrorCode.BUDGET_EXCEEDED,
                retryable=False,
            )

        headers = self._auth_headers(model)
        url = f"{self.base_url}{endpoint}"

        await self.cache.semaphore.acquire()
        try:
            for attempt in range(self.max_retries):
                try:
                    timeout_obj = aiohttp.ClientTimeout(total=timeout)
                    async with aiohttp.ClientSession(timeout=timeout_obj) as session:
                        if files:
                            data = aiohttp.FormData()
                            for name, (fname, fobj, ftype) in files.items():
                                if hasattr(fobj, "read"):
                                    content = fobj.read()
                                    fobj.seek(0)
                                else:
                                    content = fobj
                                data.add_field(
                                    name, content, filename=fname, content_type=ftype
                                )
                            if json_data:
                                for k, v in json_data.items():
                                    if k not in files:
                                        data.add_field(k, str(v))
                            auth_header = {"Authorization": headers["Authorization"]}
                            async with session.post(
                                url, headers=auth_header, data=data
                            ) as resp:
                                result = await self._handle_response(
                                    resp, model, attempt
                                )
                        else:
                            json_headers = {
                                **headers, "Content-Type": "application/json"
                            }
                            async with session.post(
                                url, headers=json_headers, json=json_data
                            ) as resp:
                                result = await self._handle_response(
                                    resp, model, attempt
                                )

                        cost = self._calc_cost(model, json_data, files, result)
                        if not self.ledger.add(cost):
                            msg = (
                                f"Budget exceeded: cost {cost:.4f} INR "
                                "would exceed limit"
                            )
                            raise BodhanError(
                                msg, code=ErrorCode.BUDGET_EXCEEDED, retryable=False
                            )

                        self.cache.set(
                            model, endpoint, json_data or {}, input_bytes, result, cost
                        )
                        return APIResponse(data=result, cost_inr=cost, cached=False)

                except BodhanError:
                    raise
                except aiohttp.ClientError as e:
                    if attempt == self.max_retries - 1:
                        raise BodhanError(
                            f"Network error after {self.max_retries} attempts: {e}",
                            code=ErrorCode.NETWORK_ERROR,
                            retryable=True,
                        )
                    await asyncio.sleep(2 ** attempt)

            raise BodhanError(
                "Max retries exceeded",
                code=ErrorCode.SERVER_ERROR,
                retryable=True,
            )
        finally:
            self.cache.semaphore.release()

    async def _handle_response(
        self,
        resp: aiohttp.ClientResponse,
        model: str,
        attempt: int,
    ) -> Any:
        if resp.status == 401:
            raise BodhanError(
                f"Authentication failed for {model}",
                code=ErrorCode.AUTHENTICATION_FAILED,
                retryable=False,
            )
        if resp.status == 429:
            retry_after = resp.headers.get("Retry-After")
            delay = (
                int(retry_after)
                if retry_after and retry_after.isdigit()
                else 2 ** attempt
            )
            await asyncio.sleep(delay)
            raise BodhanError(
                "Rate limited",
                code=ErrorCode.RATE_LIMITED,
                retryable=True,
            )
        if resp.status >= 500:
            await asyncio.sleep(2 ** attempt)
            raise BodhanError(
                f"Server error: {resp.status}",
                code=ErrorCode.SERVER_ERROR,
                retryable=True,
            )
        if resp.status >= 400:
            text = await resp.text()
            raise BodhanError(
                f"API error {resp.status}: {text}",
                code=ErrorCode.UNKNOWN,
                retryable=False,
            )
        content_type = resp.headers.get("Content-Type", "")
        if "audio" in content_type or "octet-stream" in content_type:
            return await resp.read()
        return await resp.json()

    async def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
) -> APIResponse:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        files = {"file": (audio_path.name, audio_bytes, "audio/wav")}
        json_data = {"model": "indic-transcribe"}
        if language:
            json_data["language"] = language
        return await self.request(
            "indic-transcribe", "/audio/transcriptions", json_data, files
        )

    async def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        script: str = "native",
    ) -> APIResponse:
        json_data = {
            "text": text,
            "source_language": source_language,
            "target_language": target_language,
            "script": script,
        }
        return await self.request("indic-translate", "/translate", json_data)

    async def speak(
        self,
        text: str,
        voice: str,
        instructions: str | None = None,
    ) -> APIResponse:
        json_data = {"input": text, "voice": voice, "response_format": "wav"}
        if instructions:
            json_data["instructions"] = instructions
        return await self.request("indic-speak", "/audio/speech", json_data)

    async def ocr(
        self,
        image_base64: str,
        table_format: str = "markdown",
    ) -> APIResponse:
        json_data = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_base64}"
                            },
                        }
                    ],
                }
            ],
            "table_format": table_format,
        }
        return await self.request("indic-ocr", "/chat/completions", json_data)

    async def transliterate(
        self,
        text: str,
        language: str,
        script: str = "roman",
    ) -> APIResponse:
        json_data = {
            "text": text,
            "language": language,
            "script": script,
        }
        return await self.request("indic-transliterate", "/transliterate", json_data)


client = BodhanClient()


class BodhanError(Exception):
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.UNKNOWN,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}
