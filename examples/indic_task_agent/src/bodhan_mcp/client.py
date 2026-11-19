"""Async client for the four hosted Bodhan models.

Contracts were verified against the live API on 2026-11-19 (docs/api_notes.md):

* transcribe  POST /v1/audio/transcriptions  multipart, <=30 s audio, returns {"text"}
* translate   POST /v1/chat/completions model=indic-translate, one user turn,
              `target_language` (omitting it silently defaults to Hindi, so we always
              send it), finish_reason == "length" means truncated. The documented
              POST /translate route returned 404 during development.
* speak       POST /v1/audio/speech, `instructions` is a JSON *string*; returns WAV
              bytes (the Content-Type header said audio/mpeg, so we sniff bytes).
              Unknown voices returned HTTP 500, so voices are validated client-side.
* ocr         POST /v1/chat/completions model=indic-ocr, one image_url data URI,
              returns Markdown plus top-level `blocks`.

Every call: cache lookup -> budget reservation -> concurrency slot -> bounded
retries with backoff -> cost committed (or marked uncertain) -> trace event.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from . import errors as E
from .config import BODHAN_BASE_URL, KEY_ENV, MODEL_OCR, MODEL_SPEAK, MODEL_TRANSCRIBE, MODEL_TRANSLATE, BodhanKeys
from .runtime import (
    CostLedger,
    ResultCache,
    cache_key,
    price_ocr,
    price_speak,
    price_transcribe,
    price_translate,
    sha256_bytes,
)

TraceFn = Callable[[dict[str, Any]], None]


class _Attempt(Exception):
    """Internal: one failed attempt, with how it should be billed and whether to retry."""

    def __init__(self, error: E.ToolError, billing: str, retry: bool, wait: float | None = None, status: int | None = None):
        super().__init__(error.message)
        self.error, self.billing, self.retry, self.wait, self.status = error, billing, retry, wait, status


class BodhanClient:
    def __init__(
        self,
        keys: BodhanKeys,
        ledger: CostLedger,
        cache: ResultCache,
        trace: TraceFn | None = None,
        *,
        timeout: float = 60.0,
        concurrency: int = 4,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
        base_url: str = BODHAN_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.keys = keys
        self.ledger = ledger
        self.cache = cache
        self.trace = trace
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.backoff_base = backoff_base
        self.base_url = base_url.rstrip("/")
        self._sems = {m: asyncio.Semaphore(max(1, concurrency)) for m in KEY_ENV}
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)), transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ core
    async def _call(
        self,
        model: str,
        purpose: str,
        key_params: dict[str, Any],
        payload_hash: str,
        reserve: float,
        send: Callable[[str], Awaitable[httpx.Response]],
        parse: Callable[[httpx.Response], tuple[dict, bytes | None]],
        cost_of: Callable[[dict], float],
    ) -> tuple[dict, bytes | None, dict]:
        key = cache_key(model, payload_hash, key_params)
        hit = self.cache.get(key)
        if hit:
            meta, blob, origin = hit
            self.ledger.cache_hits += 1
            info = {"model": model, "cache": origin, "cost_inr": 0.0, "attempts": 0, "latency_s": 0.0}
            self._emit({"type": "api_call", "purpose": purpose, **info})
            return meta, blob, info

        api_key = self.keys.for_model(model)
        if not api_key:
            raise E.ToolError(E.MISSING_CREDENTIALS, f"{KEY_ENV[model]} is not set, so {model} cannot be called.",
                              hint=f"Set {KEY_ENV[model]} in the environment or .env (keys are per model, from console.bodhan.ai).")

        last: _Attempt | None = None
        started = time.monotonic()
        auth_retried = False
        for attempt in range(1, self.max_attempts + 1):
            reservation = self.ledger.reserve(model, reserve, purpose)
            t0 = time.monotonic()
            status: int | None = None
            try:
                async with self._sems[model]:
                    try:
                        resp = await send(api_key)
                    except httpx.ConnectError as exc:
                        raise _Attempt(E.ToolError(E.UPSTREAM_UNAVAILABLE, f"Could not connect to Bodhan ({type(exc).__name__}).",
                                                   retryable=True, hint="Network or API outage; retry later."), "none", True) from exc
                    except httpx.TimeoutException as exc:
                        raise _Attempt(E.ToolError(E.UPSTREAM_TIMEOUT, f"{model} did not answer within {self.timeout:.0f}s.",
                                                   retryable=True, hint="Retry later or with a smaller input."), "uncertain", True) from exc
                    except httpx.HTTPError as exc:
                        raise _Attempt(E.ToolError(E.UPSTREAM_UNAVAILABLE, f"Transport error talking to {model}: {type(exc).__name__}.",
                                                   retryable=True), "uncertain", True) from exc
                status = resp.status_code
                if status >= 400:
                    raise self._classify(model, resp, auth_retried)
                try:
                    meta, blob = parse(resp)
                except E.ToolError as exc:
                    raise _Attempt(exc, "billed_unusable", exc.retryable, status=status) from exc
                except Exception as exc:  # malformed body
                    raise _Attempt(E.ToolError(E.UPSTREAM_UNAVAILABLE, f"{model} returned an unreadable response: {exc}",
                                               retryable=True), "uncertain", True, status=status) from exc
                cost = cost_of(meta)
                self.ledger.commit(reservation, cost)
                self.cache.put(key, meta, blob)
                info = {"model": model, "cache": "miss", "cost_inr": round(cost, 5), "attempts": attempt,
                        "latency_s": round(time.monotonic() - started, 3)}
                self._emit({"type": "api_call", "model": model, "purpose": purpose, "cache": "miss", "attempt": attempt, "status": status,
                            "billing": "billed", "latency_s": round(time.monotonic() - t0, 3), "cost_inr": round(cost, 5)})
                return meta, blob, info
            except _Attempt as failure:
                if failure.billing in ("uncertain", "billed_unusable"):
                    self.ledger.mark_uncertain(reservation)
                else:
                    self.ledger.release(reservation)
                if failure.error.code == E.AUTH_FAILED and not auth_retried and failure.retry:
                    auth_retried = True
                last = failure
                will_retry = failure.retry and attempt < self.max_attempts
                self._emit({"type": "api_call", "model": model, "purpose": purpose, "cache": "miss", "attempt": attempt, "status": failure.status or status,
                            "billing": failure.billing, "error": failure.error.code, "message": failure.error.message[:300],
                            "latency_s": round(time.monotonic() - t0, 3),
                            "cost_inr": round(reservation.amount, 5) if failure.billing != "none" else 0.0,
                            "will_retry": will_retry})
                if not will_retry:
                    break
                wait = failure.wait if failure.wait is not None else self.backoff_base * (2 ** (attempt - 1))
                await asyncio.sleep(min(wait, 30.0) + random.uniform(0, self.backoff_base * 0.25))
        assert last is not None
        err = last.error
        err.details.setdefault("attempts", attempt)
        raise err

    def _classify(self, model: str, resp: httpx.Response, auth_retried: bool) -> _Attempt:
        status = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else None) or (body.get("detail") if isinstance(body, dict) else None) or resp.text[:300]
        msg = str(msg)[:500]
        low = msg.lower()
        if status == 429:
            if any(w in low for w in ("credit", "balance", "insufficient", "quota", "budget", "top up", "top-up")):
                return _Attempt(E.ToolError(E.CREDIT_EXHAUSTED, f"Bodhan account credit is exhausted for {model}: {msg}",
                                            hint="Top up credit in the Bodhan console; retrying will not help."), "none", False, status=status)
            ra = resp.headers.get("retry-after")
            try:
                wait = float(ra) if ra else None
            except ValueError:
                wait = None
            return _Attempt(E.ToolError(E.RATE_LIMITED, f"{model} rate limit hit: {msg}", retryable=True,
                                        hint="Back off and retry; reduce concurrency."), "none", True, wait, status)
        if status in (401, 403):
            return _Attempt(E.ToolError(E.AUTH_FAILED, f"{model} rejected the API key ({status}): {msg}",
                                        hint=f"Check {KEY_ENV[model]}; keys are per model."), "none", not auth_retried, 1.0, status)
        if status == 502 and "max_tokens" in low:  # OCR: a layout block exceeded max_tokens; same request would fail again
            return _Attempt(E.ToolError(E.TRUNCATED_OUTPUT, f"{model}: {msg}", hint="Retry with a larger max_tokens."), "uncertain", False, status=status)
        if status >= 500:
            return _Attempt(E.ToolError(E.UPSTREAM_UNAVAILABLE, f"{model} server error {status}: {msg}", retryable=True,
                                        hint="Transient upstream failure; retried with backoff."), "uncertain", True, status=status)
        if "unsupported language" in low or "unsupported_language" in low:
            return _Attempt(E.ToolError(E.UNSUPPORTED_LANGUAGE, f"{model}: {msg}"), "none", False, status=status)
        return _Attempt(E.ToolError(E.UPSTREAM_REJECTED, f"{model} rejected the request ({status}): {msg}",
                                    hint="Fix the input; retrying the same request will fail again."), "none", False, status=status)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.trace:
            self.trace(event)

    # ----------------------------------------------------------------- models
    async def transcribe(self, wav: bytes, seconds: float, language: str | None, *, purpose: str = "transcribe") -> tuple[dict, dict]:
        params = {"language": language or "auto"}

        async def send(key: str) -> httpx.Response:
            data = {"model": MODEL_TRANSCRIBE}
            if language:
                data["language"] = language
            return await self._http.post(f"{self.base_url}/v1/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                                         data=data, files={"file": ("audio.wav", wav, "audio/wav")})

        def parse(resp: httpx.Response) -> tuple[dict, None]:
            body = resp.json()
            if not isinstance(body, dict) or "text" not in body:
                raise ValueError("missing 'text'")
            return {"text": str(body.get("text") or ""), "seconds": seconds}, None

        meta, _, info = await self._call(MODEL_TRANSCRIBE, purpose, params, sha256_bytes(wav), price_transcribe(seconds), send, parse,
                                         lambda m: price_transcribe(m["seconds"]))
        return meta, info

    async def translate(self, text: str, target: str, *, script: str = "native", max_output_tokens: int | None = None,
                        purpose: str = "translate") -> tuple[dict, dict]:
        # Rough upper bound: Indic scripts can take several tokens per source word.
        budget_tokens = max_output_tokens or min(8192, max(256, int(len(text) * 1.5) + 64))
        params = {"target_language": target, "script": script, "max_output_tokens": budget_tokens}

        async def send(key: str) -> httpx.Response:
            body: dict[str, Any] = {"model": MODEL_TRANSLATE, "messages": [{"role": "user", "content": text}],
                                    "target_language": target, "max_output_tokens": budget_tokens}
            if script != "native":
                body["script"] = script
            return await self._http.post(f"{self.base_url}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)

        def parse(resp: httpx.Response) -> tuple[dict, None]:
            body = resp.json()
            choice = body["choices"][0]
            out = choice["message"]["content"] or ""
            finish = choice.get("finish_reason")
            usage = body.get("usage") or {}
            return {"translation": out, "finish_reason": finish, "truncated": finish == "length",
                    "completion_tokens": usage.get("completion_tokens"), "budget_tokens": budget_tokens}, None

        def cost(m: dict) -> float:
            toks = m.get("completion_tokens")
            return price_translate(int(toks) if toks is not None else m["budget_tokens"])

        meta, _, info = await self._call(MODEL_TRANSLATE, purpose, params, sha256_bytes(text.encode("utf-8")),
                                         price_translate(budget_tokens), send, parse, cost)
        return meta, info

    async def speak(self, text: str, voice: str, lang: str, style: str | None = None, *, purpose: str = "speak") -> tuple[bytes, dict]:
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        params = {"voice": voice, "instructions": instructions}

        async def send(key: str) -> httpx.Response:
            return await self._http.post(f"{self.base_url}/v1/audio/speech", headers={"Authorization": f"Bearer {key}"},
                                         json={"model": MODEL_SPEAK, "input": text, "voice": voice,
                                               "instructions": json.dumps(instructions, ensure_ascii=False)})

        def parse(resp: httpx.Response) -> tuple[dict, bytes]:
            data = resp.content
            fmt = sniff_audio(data)
            if fmt is None or len(data) < 1000:
                raise E.ToolError(E.EMPTY_RESULT, f"indic-speak returned {len(data)} bytes that are not audio.", retryable=True)
            return {"format": fmt, "chars": len(text)}, data

        meta, blob, info = await self._call(MODEL_SPEAK, purpose, params, sha256_bytes(text.encode("utf-8")), price_speak(len(text)),
                                            send, parse, lambda m: price_speak(m["chars"]))
        assert blob is not None
        info["format"] = meta["format"]
        return blob, info

    async def ocr(self, image: bytes, mime: str, *, table_format: str = "markdown", max_tokens: int = 2048,
                  purpose: str = "ocr") -> tuple[dict, dict]:
        params = {"table_format": table_format, "max_tokens": max_tokens}

        async def send(key: str) -> httpx.Response:
            url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
            body = {"model": MODEL_OCR, "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}],
                    "table_format": table_format, "max_tokens": max_tokens}
            return await self._http.post(f"{self.base_url}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)

        def parse(resp: httpx.Response) -> tuple[dict, None]:
            body = resp.json()
            choice = body["choices"][0]
            blocks = [{k: b.get(k) for k in ("order", "label", "type", "bbox_xyxy", "conf", "text")} for b in body.get("blocks") or []]
            return {"markdown": choice["message"]["content"] or "", "finish_reason": choice.get("finish_reason"), "blocks": blocks}, None

        meta, _, info = await self._call(MODEL_OCR, purpose, params, sha256_bytes(image), price_ocr(1), send, parse, lambda m: price_ocr(1))
        return meta, info


def sniff_audio(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    return None
