"""Async client for the four Bodhan endpoints.

#Plan
- One method per model, each taking already-sized input (the tools do the chunking).
- Every call: cache lookup, budget reservation, a per-model concurrency limit, retries with exponential backoff
  that honours Retry-After on 429, then the actual cost is booked in the ledger.
- HTTP failures become ToolErrors with stable codes (413 to input_too_long, 415 to unsupported_format, ...),
  so callers get something actionable instead of a stack trace.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

import httpx

from . import ledger as costs
from .cache import DiskCache, make_key
from .config import Settings
from .errors import ToolError
from .ledger import Ledger

log = logging.getLogger("bodhan_mcp.client")

RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class CallEvent:
    model: str
    endpoint: str
    cache_hit: bool
    latency_ms: int
    cost_inr: float
    attempts: int


class BodhanClient:
    def __init__(self, settings: Settings, ledger: Ledger, cache: DiskCache | None = None, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.ledger = ledger
        self.cache = cache or DiskCache(settings.cache_dir)
        self._http = http
        self._sems: dict[str, asyncio.Semaphore] = {}
        self.events: list[CallEvent] = []

    # ------------------------------------------------------------------ plumbing
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.settings.timeout_s)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _sem(self, model: str) -> asyncio.Semaphore:
        if model not in self._sems:
            self._sems[model] = asyncio.Semaphore(self.settings.max_parallel)
        return self._sems[model]

    def _headers(self, model: str) -> dict[str, str]:
        key = self.settings.key_for(model)
        if not key:
            from .config import MODEL_KEY_ENV

            raise ToolError(
                "missing_api_key",
                f"No API key for {model}.",
                hint=f"Set {MODEL_KEY_ENV[model]} (or BODHAN_API_KEY) in the environment or .env file.",
            )
        return {"Authorization": f"Bearer {key}"}

    async def _send(self, model: str, method: str, path: str, *, retry_502: bool = True, **kwargs: Any) -> httpx.Response:
        url = f"{self.settings.base_url}{path}"
        headers = {**self._headers(model), **kwargs.pop("headers", {})}
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._sem(model):
                    resp = await self._client().request(method, url, headers=headers, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt > self.settings.max_retries:
                    raise ToolError("upstream_timeout", f"{model} timed out after {attempt} attempts.", retryable=True,
                                    hint="Retry later or send a smaller input.") from exc
                await asyncio.sleep(self._backoff(attempt, None))
                continue
            except httpx.TransportError as exc:
                if attempt > self.settings.max_retries:
                    raise ToolError("upstream_error", f"Could not reach {model}: {exc.__class__.__name__}.", retryable=True,
                                    hint="Check the network and BODHAN_BASE_URL.") from exc
                await asyncio.sleep(self._backoff(attempt, None))
                continue
            if resp.status_code < 400:
                resp.extensions["attempts"] = attempt
                return resp
            retryable = resp.status_code in RETRY_STATUSES and (retry_502 or resp.status_code != 502)
            if retryable and attempt <= self.settings.max_retries:
                delay = self._backoff(attempt, resp.headers.get("retry-after"))
                log.warning("retrying", extra={"model": model, "status": resp.status_code, "attempt": attempt, "delay_s": delay})
                await asyncio.sleep(delay)
                continue
            raise self._http_error(model, resp)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(60.0, float(retry_after))
            except ValueError:
                pass
        return min(30.0, 1.0 * 2 ** (attempt - 1)) + random.uniform(0, 0.5)

    @staticmethod
    def _http_error(model: str, resp: httpx.Response) -> ToolError:
        message, api_code, request_id = resp.text[:300], None, None
        try:
            err = resp.json().get("error", {})
            message = err.get("message") or message
            api_code, request_id = err.get("code"), err.get("request_id")
        except (ValueError, AttributeError):
            pass
        details = {"status": resp.status_code, "api_code": api_code, "request_id": request_id}
        status = resp.status_code
        if status in (401, 403):
            return ToolError("missing_api_key", f"{model} rejected the API key ({status}).", hint="Check the key for this model in .env.", details=details)
        if status == 413:
            return ToolError("input_too_long", f"{model}: input too large ({message}).", hint="Send a shorter input.", details=details)
        if status == 415:
            return ToolError("unsupported_format", f"{model}: unsupported media type ({message}).", details=details)
        if status == 429:
            return ToolError("rate_limited", f"{model} is rate limiting requests.", retryable=True, hint="Wait and retry later.", details=details)
        if status in (400, 404, 422):
            return ToolError("invalid_argument", f"{model} rejected the request ({status}): {message}", details=details)
        return ToolError("upstream_error", f"{model} failed with HTTP {status}: {message}", retryable=status >= 500, details=details)

    def _event(self, model: str, endpoint: str, t0: float, cost: float, cache_hit: bool, attempts: int) -> None:
        ev = CallEvent(model, endpoint, cache_hit, int((time.perf_counter() - t0) * 1000), round(cost, 5), attempts)
        self.events.append(ev)
        log.info("bodhan_call", extra=ev.__dict__)

    # ------------------------------------------------------------------ models
    async def transcribe(self, wav: bytes, seconds: float, language: str | None) -> str:
        model, endpoint = "indic-transcribe", "/v1/audio/transcriptions"
        params = {"language": language}
        key = make_key(model, endpoint, params, wav)
        t0 = time.perf_counter()
        cached = self.cache.get_json(key)
        if cached is not None:
            self.ledger.record(model, 0, cache_hit=True, units=seconds, unit="s")
            self._event(model, endpoint, t0, 0, True, 0)
            return cached["text"]
        est = costs.estimate_transcribe(seconds)
        self.ledger.reserve(model, est)
        try:
            data = {"model": model}
            if language:
                data["language"] = language
            resp = await self._send(model, "POST", endpoint, files={"file": ("audio.wav", wav, "audio/wav")}, data=data)
        finally:
            self.ledger.release(est)
        text = (resp.json().get("text") or "").strip()
        self.cache.put_json(key, {"text": text})
        self.ledger.record(model, est, cache_hit=False, units=seconds, unit="s")
        self._event(model, endpoint, t0, est, False, resp.extensions.get("attempts", 1))
        return text

    async def translate(self, text: str, target: str, script: str = "native") -> dict[str, Any]:
        model, endpoint = "indic-translate", "/translate"
        params = {"target_language": target, "script": script}
        key = make_key(model, endpoint, params, text.encode())
        t0 = time.perf_counter()
        cached = self.cache.get_json(key)
        if cached is not None:
            self.ledger.record(model, 0, cache_hit=True, units=0, unit="output_tokens")
            self._event(model, endpoint, t0, 0, True, 0)
            return cached
        est = costs.estimate_translate(max(64, len(text)))  # generous: about one token per source character
        self.ledger.reserve(model, est)
        try:
            resp = await self._send(model, "POST", endpoint, json={"text": text, "target_language": target, "script": script})
        finally:
            self.ledger.release(est)
        body = resp.json()
        usage = body.get("usage") or {}
        out_tokens = usage.get("output_tokens") or usage.get("completion_tokens") or max(1, len(body.get("translation", "")) // 2)
        cost = costs.estimate_translate(int(out_tokens))
        result = {"translation": body.get("translation", ""), "truncated": bool(body.get("truncated")), "output_tokens": int(out_tokens)}
        self.cache.put_json(key, result)
        self.ledger.record(model, cost, cache_hit=False, units=out_tokens, unit="output_tokens")
        self._event(model, endpoint, t0, cost, False, resp.extensions.get("attempts", 1))
        return result

    async def speak(self, text: str, voice: str, lang: str, style: str | None = None) -> bytes:
        model, endpoint = "indic-speak", "/v1/audio/speech"
        instructions = {"lang": lang, **({"style": style} if style else {})}
        params = {"voice": voice, "instructions": instructions}
        key = make_key(model, endpoint, params, text.encode())
        t0 = time.perf_counter()
        cached = self.cache.get_bytes(key)
        if cached is not None:
            self.ledger.record(model, 0, cache_hit=True, units=len(text), unit="chars")
            self._event(model, endpoint, t0, 0, True, 0)
            return cached
        est = costs.estimate_speak(len(text))
        self.ledger.reserve(model, est)
        try:
            resp = await self._send(
                model, "POST", endpoint,
                json={"model": model, "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False)},
            )
        finally:
            self.ledger.release(est)
        audio = resp.content
        if not audio:
            raise ToolError("upstream_error", "indic-speak returned empty audio.", retryable=True)
        self.cache.put_bytes(key, audio)
        self.ledger.record(model, est, cache_hit=False, units=len(text), unit="chars")
        self._event(model, endpoint, t0, est, False, resp.extensions.get("attempts", 1))
        return audio

    async def ocr(self, png: bytes, table_format: str = "markdown") -> dict[str, Any]:
        model, endpoint = "indic-ocr", "/v1/chat/completions"
        params = {"table_format": table_format}
        key = make_key(model, endpoint, params, png)
        t0 = time.perf_counter()
        cached = self.cache.get_json(key)
        if cached is not None:
            self.ledger.record(model, 0, cache_hit=True, units=1, unit="images")
            self._event(model, endpoint, t0, 0, True, 0)
            return cached
        est = costs.estimate_ocr(1)
        self.ledger.reserve(model, est)
        data_uri = "data:image/png;base64," + base64.b64encode(png).decode()
        try:
            resp = await self._send(
                model, "POST", endpoint, retry_502=False,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri}}]}],
                    "table_format": table_format,
                },
            )
        finally:
            self.ledger.release(est)
        body = resp.json()
        content = ""
        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            pass
        result = {"text": content, "blocks": body.get("blocks") or []}
        self.cache.put_json(key, result)
        self.ledger.record(model, est, cache_hit=False, units=1, unit="images")
        self._event(model, endpoint, t0, est, False, resp.extensions.get("attempts", 1))
        return result
