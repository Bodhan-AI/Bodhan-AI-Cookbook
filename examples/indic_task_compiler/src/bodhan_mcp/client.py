"""Async HTTP client for Bodhan APIs with per-model keys, retries, concurrency cap, and caching."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from bodhan_mcp.cache import DiskCache, make_cache_key, sha256_bytes, sha256_text
from bodhan_mcp.pricing import (
    DevSpendCapExceeded,
    DevSpendLedger,
    cost_ocr,
    cost_speak,
    cost_transcribe,
    cost_translate,
    cost_transliterate,
    estimate_output_tokens,
)

logger = logging.getLogger("bodhan_mcp.client")

MODEL_KEY_ENV: dict[str, str] = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-transliterate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}


class BodhanAPIError(Exception):
    """Structured error raised by BodhanClient."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        hint: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.hint = hint
        self.request_id = request_id


@dataclass
class BodhanCallResult:
    model: str
    payload: dict[str, Any]
    binary: bytes | None = None
    cost_inr: float = 0.0
    cache_hit: bool = False
    latency_ms: int = 0
    attempts: int = 1


BudgetHook = Callable[[str, float, str], Awaitable[None]]


class BodhanClient:
    """Async client for Bodhan's Indic models.

    Handles:
      - Per-model key lookup with BODHAN_API_KEY fallback (never logs keys)
      - Content-addressed disk cache (sha256 of model + canonical params + input hash)
      - Concurrency limit via asyncio.Semaphore
      - Exponential backoff + jitter on 429 / 5xx / timeout (honoring Retry-After)
      - Single retry on 401 only if the same key succeeded earlier in the run
      - Optional budget reservation hook & dev spend ledger enforcement
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        keys: dict[str, str] | None = None,
        cache: DiskCache | None = None,
        timeout_s: float | None = None,
        max_concurrency: int | None = None,
        max_retries: int = 4,
        base_backoff_s: float = 0.6,
        transport: httpx.AsyncBaseTransport | None = None,
        budget_hook: BudgetHook | None = None,
        dev_ledger: DevSpendLedger | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/")
        self._explicit_keys = dict(keys or {})
        self.cache = cache if cache is not None else DiskCache()
        self.timeout_s = timeout_s if timeout_s is not None else float(os.environ.get("BODHAN_TIMEOUT_S", "90"))
        conc = max_concurrency if max_concurrency is not None else int(os.environ.get("BODHAN_MAX_CONCURRENCY", "4"))
        self._semaphore = asyncio.Semaphore(max(1, conc))
        self.max_retries = max_retries
        self.base_backoff_s = base_backoff_s
        self._transport = transport
        self._budget_hook = budget_hook
        self._dev_ledger = dev_ledger if dev_ledger is not None else (None if transport is not None else DevSpendLedger.from_env())
        self._succeeded_key_hashes: set[str] = set()
        self._client: httpx.AsyncClient | None = None

    def set_budget_hook(self, hook: BudgetHook | None) -> None:
        self._budget_hook = hook

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout_s),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def _get_key(self, model: str) -> str:
        if model in self._explicit_keys and self._explicit_keys[model]:
            return self._explicit_keys[model]
        env_name = MODEL_KEY_ENV.get(model, "BODHAN_API_KEY")
        val = os.environ.get(env_name) or os.environ.get("BODHAN_API_KEY")
        if val:
            return val
        if self._transport is not None:
            return "fake-test-key"
        raise BodhanAPIError(
            code="missing_api_key",
            message=f"No API key configured for {model}. Set {env_name} or BODHAN_API_KEY.",
            retryable=False,
            hint=f"Export {env_name} (or BODHAN_API_KEY) in your environment or .env file.",
        )

    @staticmethod
    def _parse_error(resp: httpx.Response) -> tuple[str, str, str | None]:
        try:
            body = resp.json()
            err = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(err, dict) and err.get("message"):
                return (
                    str(err.get("code") or f"http_{resp.status_code}"),
                    str(err["message"]),
                    err.get("request_id"),
                )
        except Exception:
            pass
        snippet = (resp.text or "")[:300].strip()
        return f"http_{resp.status_code}", snippet or f"HTTP {resp.status_code}", None

    async def _send_with_retry(
        self,
        model: str,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        estimated_cost_inr: float = 0.0,
        detail: str = "",
    ) -> tuple[httpx.Response, int, int]:
        key = self._get_key(model)
        key_hash = sha256_text(key)
        headers = {"Authorization": f"Bearer {key}"}

        if self._dev_ledger is not None:
            try:
                self._dev_ledger.check_allowed(estimated_cost_inr)
            except DevSpendCapExceeded as exc:
                raise BodhanAPIError(
                    code="dev_spend_cap_exceeded",
                    message=str(exc),
                    retryable=False,
                    hint="Increase BODHAN_DEV_SPEND_CAP_INR or rely on cached outputs.",
                ) from exc

        if self._budget_hook is not None:
            await self._budget_hook(model, estimated_cost_inr, detail)

        client = await self._http()
        t0 = time.monotonic()
        attempts = 0
        retried_401 = False

        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            try:
                async with self._semaphore:
                    resp = await client.request(
                        method,
                        path,
                        headers=headers,
                        json=json_body,
                        data=data,
                        files=files,
                    )
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                if attempt < self.max_retries:
                    delay = self.base_backoff_s * (2**attempt) + random.uniform(0, 0.25)
                    logger.warning("Transient network error on %s (%s); retry %d in %.2fs", model, type(exc).__name__, attempt + 1, delay)
                    await asyncio.sleep(delay)
                    continue
                raise BodhanAPIError(
                    code="timeout" if isinstance(exc, httpx.TimeoutException) else "network_error",
                    message=f"Bodhan {model} request failed after {attempts} attempts: {type(exc).__name__}",
                    retryable=True,
                    hint="Check network connectivity or increase BODHAN_TIMEOUT_S.",
                ) from exc

            if 200 <= resp.status_code < 300:
                self._succeeded_key_hashes.add(key_hash)
                latency_ms = int((time.monotonic() - t0) * 1000)
                return resp, latency_ms, attempts

            err_code, err_msg, req_id = self._parse_error(resp)
            status = resp.status_code

            # 401: retry ONCE only if this same key succeeded earlier in this run.
            if status == 401:
                if key_hash in self._succeeded_key_hashes and not retried_401:
                    retried_401 = True
                    delay = self.base_backoff_s + random.uniform(0, 0.2)
                    logger.warning("Transient 401 on %s for previously valid key; retrying once", model)
                    await asyncio.sleep(delay)
                    continue
                raise BodhanAPIError(
                    code="auth_error",
                    message=f"Authentication failed for {model} (401): {err_msg}",
                    status_code=status,
                    retryable=False,
                    hint=f"Verify {MODEL_KEY_ENV.get(model, 'BODHAN_API_KEY')} or BODHAN_API_KEY.",
                    request_id=req_id,
                )

            # 429 or 5xx: retry with backoff + jitter, respecting Retry-After.
            if status == 429 or status >= 500:
                if attempt < self.max_retries:
                    retry_after = resp.headers.get("retry-after")
                    if retry_after and retry_after.replace(".", "", 1).isdigit():
                        delay = min(float(retry_after), 30.0)
                    else:
                        delay = self.base_backoff_s * (2**attempt) + random.uniform(0, 0.3)
                    logger.warning("Bodhan %s returned %d (%s); retry %d in %.2fs", model, status, err_code, attempt + 1, delay)
                    await asyncio.sleep(delay)
                    continue
                raise BodhanAPIError(
                    code="rate_limited" if status == 429 else f"server_error_{status}",
                    message=f"Bodhan {model} returned HTTP {status} after {attempts} attempts: {err_msg}",
                    status_code=status,
                    retryable=True,
                    hint="Rate limit, credit exhaustion, or server overload. Check console.bodhan.ai or retry later.",
                    request_id=req_id,
                )

            # Other 4xx: non-retryable client/validation error.
            raise BodhanAPIError(
                code=err_code,
                message=f"Bodhan {model} rejected request ({status}): {err_msg}",
                status_code=status,
                retryable=False,
                hint="Inspect input format, language code, and size limits.",
                request_id=req_id,
            )

        raise BodhanAPIError(code="unknown_error", message=f"Unexpected retry termination for {model}", retryable=False)

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "chunk.wav",
        duration_s: float,
        language: str | None = None,
        mime_type: str = "audio/wav",
    ) -> BodhanCallResult:
        model = "indic-transcribe"
        params = {"language": language or ""}
        cache_key = make_cache_key(model, params, sha256_bytes(audio_bytes))
        cached = self.cache.get(cache_key)
        if cached is not None:
            return BodhanCallResult(model=model, payload=cached.payload, cost_inr=0.0, cache_hit=True)

        est_cost = cost_transcribe(duration_s)
        form_data: dict[str, Any] = {"model": "indic-transcribe"}
        if language:
            form_data["language"] = language

        resp, latency_ms, attempts = await self._send_with_retry(
            model,
            "POST",
            "/v1/audio/transcriptions",
            data=form_data,
            files={"file": (filename, audio_bytes, mime_type)},
            estimated_cost_inr=est_cost,
            detail=f"transcribe {filename} ({duration_s:.1f}s)",
        )
        payload = resp.json()
        actual_cost = est_cost
        if self._dev_ledger is not None:
            self._dev_ledger.record(model, actual_cost, f"{filename} {duration_s:.1f}s")
        self.cache.put(cache_key, payload, original_cost_inr=actual_cost)
        return BodhanCallResult(
            model=model,
            payload=payload,
            cost_inr=actual_cost,
            cache_hit=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    async def translate(
        self,
        text: str,
        *,
        target_language: str,
        script: str = "native",
        max_output_tokens: int | None = None,
    ) -> BodhanCallResult:
        model = "indic-translate"
        params: dict[str, Any] = {
            "target_language": target_language,
            "script": script,
            "max_output_tokens": max_output_tokens,
        }
        cache_key = make_cache_key(model, params, sha256_text(text))
        cached = self.cache.get(cache_key)
        if cached is not None:
            return BodhanCallResult(model=model, payload=cached.payload, cost_inr=0.0, cache_hit=True)

        est_tokens = estimate_output_tokens(text)
        est_cost = cost_translate(est_tokens)
        body: dict[str, Any] = {
            "text": text,
            "target_language": target_language,
            "script": script,
        }
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens

        resp, latency_ms, attempts = await self._send_with_retry(
            model,
            "POST",
            "/translate",
            json_body=body,
            estimated_cost_inr=est_cost,
            detail=f"translate -> {target_language} ({len(text)} chars)",
        )
        payload = resp.json()
        usage = payload.get("usage") or {}
        out_tokens = (
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or estimate_output_tokens(payload.get("translation", ""))
        )
        actual_cost = cost_translate(int(out_tokens))
        if self._dev_ledger is not None:
            self._dev_ledger.record(model, actual_cost, f"-> {target_language} {out_tokens} tok")
        self.cache.put(cache_key, payload, original_cost_inr=actual_cost)
        return BodhanCallResult(
            model=model,
            payload=payload,
            cost_inr=actual_cost,
            cache_hit=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    async def transliterate(
        self,
        text: str,
        *,
        language: str,
        script: str = "roman",
        max_output_tokens: int | None = None,
    ) -> BodhanCallResult:
        model = "indic-transliterate"
        params = {
            "language": language,
            "script": script,
            "max_output_tokens": max_output_tokens,
        }
        cache_key = make_cache_key(model, params, sha256_text(text))
        cached = self.cache.get(cache_key)
        if cached is not None:
            return BodhanCallResult(model=model, payload=cached.payload, cost_inr=0.0, cache_hit=True)

        est_cost = cost_transliterate(estimate_output_tokens(text))
        body: dict[str, Any] = {
            "text": text,
            "language": language,
            "script": script,
        }
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens

        resp, latency_ms, attempts = await self._send_with_retry(
            model,
            "POST",
            "/transliterate",
            json_body=body,
            estimated_cost_inr=est_cost,
            detail=f"transliterate {language}->{script}",
        )
        payload = resp.json()
        out_tokens = estimate_output_tokens(payload.get("transliteration", ""))
        actual_cost = cost_transliterate(out_tokens)
        if self._dev_ledger is not None:
            self._dev_ledger.record(model, actual_cost, f"{language}->{script}")
        self.cache.put(cache_key, payload, original_cost_inr=actual_cost)
        return BodhanCallResult(
            model=model,
            payload=payload,
            cost_inr=actual_cost,
            cache_hit=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    async def speak(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        style: str | None = None,
    ) -> BodhanCallResult:
        model = "indic-speak"
        instructions: dict[str, Any] = {"lang": language}
        if style:
            instructions["style"] = style
        params = {"voice": voice, "instructions": instructions}
        cache_key = make_cache_key(model, params, sha256_text(text))
        cached = self.cache.get(cache_key)
        if cached is not None and cached.binary is not None:
            return BodhanCallResult(
                model=model,
                payload=cached.payload,
                binary=cached.binary,
                cost_inr=0.0,
                cache_hit=True,
            )

        est_cost = cost_speak(len(text))
        body = {
            "model": "indic-speak",
            "input": text,
            "voice": voice,
            "instructions": json.dumps(instructions, ensure_ascii=False),
        }
        resp, latency_ms, attempts = await self._send_with_retry(
            model,
            "POST",
            "/v1/audio/speech",
            json_body=body,
            estimated_cost_inr=est_cost,
            detail=f"speak {language}/{voice} ({len(text)} chars)",
        )
        audio_bytes = resp.content
        payload = {"voice": voice, "language": language, "style": style, "bytes": len(audio_bytes)}
        actual_cost = est_cost
        if self._dev_ledger is not None:
            self._dev_ledger.record(model, actual_cost, f"{language}/{voice} {len(text)} chars")
        self.cache.put(cache_key, payload, binary=audio_bytes, original_cost_inr=actual_cost)
        return BodhanCallResult(
            model=model,
            payload=payload,
            binary=audio_bytes,
            cost_inr=actual_cost,
            cache_hit=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    async def ocr(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        table_format: str = "markdown",
        max_tokens: int | None = None,
    ) -> BodhanCallResult:
        model = "indic-ocr"
        params = {"mime_type": mime_type, "table_format": table_format, "max_tokens": max_tokens}
        cache_key = make_cache_key(model, params, sha256_bytes(image_bytes))
        cached = self.cache.get(cache_key)
        if cached is not None:
            return BodhanCallResult(model=model, payload=cached.payload, cost_inr=0.0, cache_hit=True)

        est_cost = cost_ocr(1)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                        }
                    ],
                }
            ],
            "table_format": table_format,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens

        resp, latency_ms, attempts = await self._send_with_retry(
            model,
            "POST",
            "/v1/chat/completions",
            json_body=body,
            estimated_cost_inr=est_cost,
            detail=f"ocr page ({len(image_bytes)} bytes)",
        )
        payload = resp.json()
        actual_cost = est_cost
        if self._dev_ledger is not None:
            self._dev_ledger.record(model, actual_cost, f"ocr {len(image_bytes)} bytes")
        self.cache.put(cache_key, payload, original_cost_inr=actual_cost)
        return BodhanCallResult(
            model=model,
            payload=payload,
            cost_inr=actual_cost,
            cache_hit=False,
            latency_ms=latency_ms,
            attempts=attempts,
        )
