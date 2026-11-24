"""Async client for Bodhan's hosted models.

Responsibilities, so that tools never deal with them:

* auth per model (``BODHAN_*_KEY``) and Bodhan's JSON error format
* retries with exponential backoff + jitter on 429/5xx/timeouts, honouring ``Retry-After``
* a per-model concurrency limit (asyncio semaphore)
* per-call timeouts
* the content-addressed cache (same input never sent to the same model twice)
* budget reservation before a call and exact cost accounting after it
"""

from __future__ import annotations

import asyncio
import base64
import email.utils
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from . import errors, pricing
from .cache import DiskCache, cache_key
from .config import Settings

log = logging.getLogger("bodhan_mcp.client")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class CallRecord:
    model: str
    cached: bool
    cost_inr: float
    latency_ms: int
    attempts: int = 1
    request_id: str | None = None
    units: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ledger:
    """Tracks the cost of one tool invocation and enforces an optional ceiling.

    ``reserve`` is called with a conservative estimate *before* a request is sent;
    ``commit`` replaces the reservation with the actual cost. Concurrent requests
    inside one tool share the ledger, so parallel chunks cannot jointly overspend.
    """

    def __init__(self, max_cost_inr: float | None = None) -> None:
        self.max_cost_inr = max_cost_inr
        self.records: list[CallRecord] = []
        self._spent = 0.0
        self._reserved = 0.0

    @property
    def spent(self) -> float:
        return round(self._spent, 6)

    def reserve(self, estimate: float, what: str) -> None:
        if self.max_cost_inr is not None and self._spent + self._reserved + estimate > self.max_cost_inr + 1e-9:
            raise errors.BodhanError(
                errors.BUDGET_EXCEEDED,
                f"{what} would cost about ₹{estimate:.4f}, exceeding the remaining budget "
                f"(₹{max(self.max_cost_inr - self._spent - self._reserved, 0):.4f} left of ₹{self.max_cost_inr:.4f}).",
                hint="Raise max_cost_inr, shorten the input, or skip this step and report partial results.",
                details={"estimate_inr": round(estimate, 6), "spent_inr": self.spent},
            )
        self._reserved += estimate

    def commit(self, estimate: float, record: CallRecord) -> None:
        self._reserved = max(0.0, self._reserved - estimate)
        self._spent += record.cost_inr
        self.records.append(record)

    def release(self, estimate: float) -> None:
        self._reserved = max(0.0, self._reserved - estimate)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(raw)
            return max(0.0, dt.timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _error_from_response(model: str, resp: httpx.Response) -> errors.BodhanError:
    code_text, message, request_id = None, resp.text[:300], resp.headers.get("x-request-id")
    try:
        err = resp.json().get("error", {})
        code_text = err.get("code")
        message = err.get("message") or message
        request_id = err.get("request_id") or request_id
    except (ValueError, AttributeError):
        pass
    details = {"status": resp.status_code, "upstream_code": code_text, "request_id": request_id, "model": model}
    s = resp.status_code
    if s in (401, 403):
        return errors.BodhanError(
            errors.AUTH_FAILED, f"{model} rejected the API key ({s}): {message}",
            hint="Check the BODHAN_*_KEY for this model; keys are issued per model in the Bodhan console.",
            details=details,
        )
    if s == 429:
        return errors.BodhanError(
            errors.RATE_LIMITED, f"{model} rate limit or monthly budget reached: {message}",
            hint="Wait and retry later, or lower BODHAN_MAX_CONCURRENCY.", retryable=True, details=details,
        )
    if s in (400, 404, 413, 415, 422):
        return errors.BodhanError(
            errors.INVALID_INPUT, f"{model} rejected the request ({s}): {message}",
            hint="The input is not acceptable to the model; check language codes, size limits and file format.",
            details=details,
        )
    return errors.BodhanError(
        errors.UPSTREAM_ERROR, f"{model} failed with HTTP {s}: {message}",
        hint="This is usually transient; retry later.", retryable=s in RETRYABLE_STATUS, details=details,
    )


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.cache = DiskCache(settings.cache_dir, enabled=settings.cache_enabled)
        self._http = httpx.AsyncClient(base_url=settings.base_url, transport=transport, timeout=60.0)
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._sleep = sleep
        self.request_count = 0
        self._translate_route = "chat"  # "chat" (live gateway) or "legacy" (POST /translate, as documented)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _sem(self, model: str) -> asyncio.Semaphore:
        if model not in self._sems:
            self._sems[model] = asyncio.Semaphore(self.settings.max_concurrency)
        return self._sems[model]

    async def _send(self, model: str, build: Callable[[], dict[str, Any]]) -> tuple[httpx.Response, int, float]:
        """POST with retries. ``build`` returns fresh httpx request kwargs per attempt."""
        headers = {"Authorization": f"Bearer {self.settings.key_for(model)}"}
        timeout = self.settings.timeouts.get(model, 120.0)
        attempts = 0
        start = time.monotonic()
        last_exc: errors.BodhanError | None = None
        async with self._sem(model):
            while attempts <= self.settings.max_retries:
                attempts += 1
                wait: float | None = None
                try:
                    self.request_count += 1
                    kwargs = build()
                    resp = await self._http.post(headers=headers, timeout=timeout, **kwargs)
                except httpx.TimeoutException as exc:
                    last_exc = errors.BodhanError(
                        errors.UPSTREAM_TIMEOUT, f"{model} did not respond within {timeout:.0f}s ({type(exc).__name__}).",
                        hint="Retry later or send a smaller input.", retryable=True, details={"model": model},
                    )
                except httpx.TransportError as exc:
                    last_exc = errors.BodhanError(
                        errors.UPSTREAM_ERROR, f"Network error talking to {model}: {exc}",
                        hint="Check connectivity to BODHAN_BASE_URL.", retryable=True, details={"model": model},
                    )
                else:
                    if resp.is_success:
                        return resp, attempts, time.monotonic() - start
                    last_exc = _error_from_response(model, resp)
                    if resp.status_code not in RETRYABLE_STATUS:
                        raise last_exc
                    wait = _retry_after_seconds(resp)
                if attempts > self.settings.max_retries:
                    break
                backoff = wait if wait is not None else min(30.0, 0.5 * (2 ** (attempts - 1))) * (0.5 + random.random())
                log.warning(
                    json.dumps({"event": "retry", "model": model, "attempt": attempts, "sleep_s": round(min(backoff, 30.0), 2),
                                "error": last_exc.code if last_exc else None})
                )
                await self._sleep(min(backoff, 30.0))
        assert last_exc is not None
        last_exc.details["attempts"] = attempts
        raise last_exc

    # ---------------------------------------------------------------------------------
    # Models
    # ---------------------------------------------------------------------------------

    async def transcribe(self, wav: bytes, *, duration_s: float, language: str | None, ledger: Ledger,
                         filename: str = "audio.wav") -> dict[str, Any]:
        model = "indic-transcribe"
        params = {"language": language}
        key = cache_key(model, params, wav)
        if (hit := self.cache.get_json(key)) is not None:
            ledger.records.append(CallRecord(model, True, 0.0, 0, 0, hit.get("request_id"), {"audio_seconds": duration_s}))
            return hit
        estimate = pricing.transcribe_cost(duration_s)
        ledger.reserve(estimate, f"Transcribing {duration_s:.1f}s of audio")
        try:
            data = {"model": model}
            if language:
                data["language"] = language
            resp, attempts, elapsed = await self._send(
                model, lambda: {"url": "/v1/audio/transcriptions", "files": {"file": (filename, wav, "audio/wav")}, "data": data}
            )
        except BaseException:
            ledger.release(estimate)
            raise
        body = resp.json()
        out = {"text": (body.get("text") or "").strip(), "request_id": resp.headers.get("x-request-id")}
        if "language" in body:
            out["language"] = body["language"]
        ledger.commit(estimate, CallRecord(model, False, estimate, int(elapsed * 1000), attempts, out["request_id"],
                                           {"audio_seconds": round(duration_s, 3)}))
        self.cache.put_json(key, out)
        return out

    async def translate(self, text: str, *, target_language: str, script: str, ledger: Ledger,
                        max_output_tokens: int | None = None) -> dict[str, Any]:
        model = "indic-translate"
        params: dict[str, Any] = {"target_language": target_language, "script": script}
        if max_output_tokens:
            params["max_output_tokens"] = max_output_tokens
        key = cache_key(model, params, text)
        if (hit := self.cache.get_json(key)) is not None:
            ledger.records.append(CallRecord(model, True, 0.0, 0, 0, hit.get("request_id"), {"output_tokens": 0}))
            return hit
        estimate = pricing.translate_cost(pricing.estimate_translation_output_tokens(text, target_language))
        ledger.reserve(estimate, f"Translating {len(text)} characters")
        # The live gateway serves indic-translate as a chat model (target_language / script as body fields;
        # greedy decoding is pinned server-side, so no max_tokens/temperature). The Cookbook documents a plain
        # POST /translate; if the chat route is missing (404) we use that instead and remember the choice.
        chat_body = {"model": model, "messages": [{"role": "user", "content": text}], **params}
        try:
            try:
                if self._translate_route == "legacy":
                    raise errors.BodhanError(errors.INVALID_INPUT, "legacy route selected", details={"status": 404})
                resp, attempts, elapsed = await self._send(model, lambda: {"url": "/v1/chat/completions", "json": chat_body})
            except errors.BodhanError as exc:
                if exc.details.get("status") != 404:
                    raise
                self._translate_route = "legacy"
                resp, attempts, elapsed = await self._send(model, lambda: {"url": "/translate", "json": {"text": text, **params}})
        except BaseException:
            ledger.release(estimate)
            raise
        body = resp.json()
        usage = body.get("usage") or {}
        if "choices" in body:
            choice = (body.get("choices") or [{}])[0]
            translation = ((choice.get("message") or {}).get("content") or "").strip()
            truncated = choice.get("finish_reason") == "length"
        else:
            translation = body.get("translation", "")
            truncated = bool(body.get("truncated", False))
        out_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or pricing.estimate_tokens(translation))
        cost = pricing.translate_cost(out_tokens)
        out = {
            "translation": translation,
            "truncated": truncated,
            "script": body.get("script", script),
            "usage": usage,
            "request_id": resp.headers.get("x-request-id") or body.get("id"),
        }
        ledger.commit(estimate, CallRecord(model, False, cost, int(elapsed * 1000), attempts, out["request_id"],
                                           {"output_tokens": out_tokens}))
        self.cache.put_json(key, out)
        return out

    async def speak(self, text: str, *, voice: str, lang: str, style: str | None, ledger: Ledger) -> bytes:
        model = "indic-speak"
        instructions = {"lang": lang, **({"style": style} if style else {})}
        params = {"voice": voice, "instructions": instructions}
        key = cache_key(model, params, text)
        if (hit := self.cache.get_bytes(key)) is not None:
            ledger.records.append(CallRecord(model, True, 0.0, 0, 0, None, {"input_chars": len(text)}))
            return hit
        cost = pricing.speak_cost(len(text))
        ledger.reserve(cost, f"Synthesising {len(text)} characters of speech")
        body = {"model": model, "input": text, "voice": voice, "instructions": json.dumps(instructions, ensure_ascii=False)}
        try:
            resp, attempts, elapsed = await self._send(model, lambda: {"url": "/v1/audio/speech", "json": body})
        except BaseException:
            ledger.release(cost)
            raise
        audio = resp.content
        if not audio:
            ledger.release(cost)
            raise errors.BodhanError(errors.EMPTY_RESULT, "indic-speak returned no audio.", retryable=True)
        ledger.commit(cost, CallRecord(model, False, cost, int(elapsed * 1000), attempts, resp.headers.get("x-request-id"),
                                       {"input_chars": len(text)}))
        self.cache.put_bytes(key, audio)
        return audio

    async def ocr(self, image: bytes, *, mime: str, table_format: str, ledger: Ledger) -> dict[str, Any]:
        model = "indic-ocr"
        params = {"table_format": table_format}
        key = cache_key(model, params, image)
        if (hit := self.cache.get_json(key)) is not None:
            ledger.records.append(CallRecord(model, True, 0.0, 0, 0, hit.get("request_id"), {"pages": 1}))
            return hit
        cost = pricing.ocr_cost(1)
        ledger.reserve(cost, "OCR of one page")
        b64 = base64.b64encode(image).decode()
        body = {
            "model": model,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
            "table_format": table_format,
        }
        try:
            resp, attempts, elapsed = await self._send(model, lambda: {"url": "/v1/chat/completions", "json": body})
        except BaseException:
            ledger.release(cost)
            raise
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            content = ""
        out = {"markdown": content, "blocks": data.get("blocks") or [], "request_id": resp.headers.get("x-request-id")}
        ledger.commit(cost, CallRecord(model, False, cost, int(elapsed * 1000), attempts, out["request_id"], {"pages": 1}))
        self.cache.put_json(key, out)
        return out
