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
from contextlib import asynccontextmanager
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


class AdaptiveGate:
    """Per-model concurrency limit. A 429 halves the limit and starts a cooldown that
    every request to that model waits out (one shared back-off instead of N requests
    hammering a busy server); three successes in a row raise the limit by one again."""

    def __init__(self, maximum: int):
        self.max = max(1, maximum)
        self.limit = self.max
        self.inflight = 0
        self.cooldown_until = 0.0
        self.ok_streak = 0
        self._cond = asyncio.Condition()

    @asynccontextmanager
    async def slot(self, deadline: float | None = None):
        """Wait for a free slot and any cooldown, but never past `deadline` (monotonic time)."""

        def remaining() -> float | None:
            return None if deadline is None else deadline - time.monotonic()

        def expired() -> E.ToolError:
            return E.ToolError(E.UPSTREAM_TIMEOUT, "The run's time limit was reached while waiting for the API rate limit to clear.",
                               retryable=True, hint="Stop and report what was done; retry later with a longer --max-seconds.")

        async with self._cond:
            while True:
                left = remaining()
                if left is not None and left <= 0:
                    raise expired()
                wait = self.cooldown_until - time.monotonic()
                if wait > 0:
                    self._cond.release()
                    try:
                        await asyncio.sleep(wait if left is None else min(wait, left))
                    finally:
                        await self._cond.acquire()
                    continue
                if self.inflight < self.limit:
                    break
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=left)
                except TimeoutError as exc:
                    raise expired() from exc
            self.inflight += 1
        try:
            yield
        finally:
            async with self._cond:
                self.inflight -= 1
                self._cond.notify_all()

    def rate_limited(self, wait: float) -> None:
        self.limit = max(1, self.limit // 2)
        self.ok_streak = 0
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + wait)

    def succeeded(self) -> None:
        self.ok_streak += 1
        if self.ok_streak >= 3 and self.limit < self.max:
            self.limit += 1
            self.ok_streak = 0


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
        max_rate_limit_attempts: int = 7,
        backoff_base: float = 1.0,
        base_url: str = BODHAN_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        deadline: float | None = None,
    ):
        self.keys = keys
        self.ledger = ledger
        self.cache = cache
        self.trace = trace
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.max_rate_limit_attempts = max(self.max_attempts, max_rate_limit_attempts)
        self.backoff_base = backoff_base
        self.base_url = base_url.rstrip("/")
        self.gates = {m: AdaptiveGate(concurrency) for m in KEY_ENV}
        self._inflight: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Future] = set()
        self._waiters: dict[str, int] = {}
        self.deadline = deadline  # monotonic time after which no request may wait or retry (set by the agent)
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)), transport=transport)

    async def cancel_all(self) -> None:
        """Cancel every request still in flight and wait until each one has settled its cost
        (released if never sent, uncertain if sent). Used when the run's deadline passes."""
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        await self.cancel_all()
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
        if hit and not _valid_meta(model, hit[0], hit[1]):
            hit = None  # a well-formed entry with unusable content: refetch rather than fail
        if hit:
            meta, blob, origin = hit
            self.ledger.cache_hits += 1
            info = {"model": model, "cache": origin, "cost_inr": 0.0, "attempts": 0, "latency_s": 0.0}
            self._emit({"type": "api_call", "purpose": purpose, **info})
            return meta, blob, info

        task = self._inflight.get(key)
        shared = task is not None
        if task is None:
            # The request is owned by the client, not by this caller: if this caller is cancelled
            # (MCP client gave up, run deadline), other callers waiting for the same input still get
            # the result, and a request already sent completes and is accounted for.
            task = asyncio.ensure_future(self._send(model, purpose, key, reserve, send, parse, cost_of))
            self._inflight[key] = task
            self._tasks.add(task)
            task.add_done_callback(lambda t, k=key: self._request_done(k, t))
        # Waiters are counted: when the last one gives up (cancelled), the request is cancelled too,
        # which settles its cost (released if unsent, uncertain if sent) instead of running orphaned.
        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            meta, blob, info = await asyncio.shield(task)
        except asyncio.CancelledError:
            if self._waiters.get(key, 0) <= 1 and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            self._waiters[key] = self._waiters.get(key, 1) - 1
            if self._waiters[key] <= 0:
                self._waiters.pop(key, None)
        if shared:
            self.ledger.cache_hits += 1
            info = {"model": model, "cache": "in_flight", "cost_inr": 0.0, "attempts": 0, "latency_s": 0.0}
            self._emit({"type": "api_call", "purpose": purpose, **info})
        return meta, blob, info

    def _request_done(self, key: str, task: asyncio.Future) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # retrieved here so an unobserved failure never logs a warning

    def is_cached(self, model: str, payload: bytes, params: dict[str, Any]) -> bool:
        """True if this exact request would be served free (cache or an identical request in flight)."""
        key = cache_key(model, sha256_bytes(payload), params)
        return key in self._inflight or self.cache.get(key) is not None

    # Keys must match the ones the model methods below build.
    def speak_cached(self, text: str, voice: str, lang: str, style: str | None = None) -> bool:
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        return self.is_cached(MODEL_SPEAK, text.encode("utf-8"), {"voice": voice, "instructions": instructions})

    def ocr_cached(self, image: bytes, table_format: str = "markdown", max_tokens: int = 2048) -> bool:
        return self.is_cached(MODEL_OCR, image, {"table_format": table_format, "max_tokens": max_tokens})

    def transcribe_cached(self, wav: bytes, language: str | None) -> bool:
        return self.is_cached(MODEL_TRANSCRIBE, wav, {"language": language or "auto"})

    async def _send(self, model, purpose, key, reserve, send, parse, cost_of) -> tuple[dict, bytes | None, dict]:
        api_key = self.keys.for_model(model)
        if not api_key:
            raise E.ToolError(E.MISSING_CREDENTIALS, f"{KEY_ENV[model]} is not set, so {model} cannot be called.",
                              hint=f"Set {KEY_ENV[model]} in the environment or .env (keys are per model, from console.bodhan.ai).")

        last: _Attempt | None = None
        started = time.monotonic()
        auth_retried = False
        attempt = 0
        while True:
            attempt += 1
            t0 = time.monotonic()
            status: int | None = None
            reservation = None
            dispatched = False
            try:
                async with self.gates[model].slot(self.deadline):
                    # Reserve once a slot is held, so queued requests do not lock up budget.
                    reservation = self.ledger.reserve(model, reserve, purpose)
                    dispatched = True
                    # Logged before sending: if the process dies mid-request, --resume counts it as uncertain spend.
                    self._emit({"type": "api_dispatch", "model": model, "purpose": purpose, "reservation_id": reservation.id,
                                "amount_inr": round(reserve, 5)})
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
                self.gates[model].succeeded()
                self.cache.put(key, meta, blob, durable=meta.get("complete", True))  # truncated output is kept for this run only
                info = {"model": model, "cache": "miss", "cost_inr": round(cost, 5), "attempts": attempt,
                        "latency_s": round(time.monotonic() - started, 3)}
                self._emit({"type": "api_call", "model": model, "purpose": purpose, "cache": "miss", "attempt": attempt, "status": status,
                            "reservation_id": reservation.id, "billing": "billed", "latency_s": round(time.monotonic() - t0, 3), "cost_inr": round(cost, 5)})
                return meta, blob, info
            except _Attempt as failure:
                assert reservation is not None
                if failure.billing in ("uncertain", "billed_unusable"):
                    self.ledger.mark_uncertain(reservation)
                else:
                    self.ledger.release(reservation)
                if failure.error.code == E.AUTH_FAILED and not auth_retried and failure.retry:
                    auth_retried = True
                last = failure
                limit = self.max_rate_limit_attempts if failure.error.code == E.RATE_LIMITED else self.max_attempts
                will_retry = failure.retry and attempt < limit
                if failure.error.code == E.RATE_LIMITED:
                    # 429s are not billed, so waiting is cheap; back off for the whole model, not just this request
                    rl_wait = min(60.0, failure.wait if failure.wait is not None else self.backoff_base * 2 ** attempt)
                    self.gates[model].rate_limited(rl_wait)
                self._emit({"type": "api_call", "model": model, "purpose": purpose, "cache": "miss", "attempt": attempt, "status": failure.status or status,
                            "reservation_id": reservation.id,
                            "billing": failure.billing, "error": failure.error.code, "message": failure.error.message[:300],
                            "latency_s": round(time.monotonic() - t0, 3),
                            "cost_inr": round(reservation.amount, 5) if failure.billing != "none" else 0.0,
                            "will_retry": will_retry})
                if not will_retry:
                    break
                if failure.error.code != E.RATE_LIMITED:  # rate limits wait inside the shared gate instead
                    wait = failure.wait if failure.wait is not None else self.backoff_base * (2 ** (attempt - 1))
                    await asyncio.sleep(min(wait, 30.0) + random.uniform(0, self.backoff_base * 0.25))
                else:
                    await asyncio.sleep(random.uniform(0, self.backoff_base * 0.5))
            except BaseException:
                # Cancellation (MCP client, run deadline) or a budget/deadline error: never leave budget locked.
                # A request that was already sent may have been billed, so it counts as uncertain.
                if reservation is not None and reservation.open:
                    (self.ledger.mark_uncertain if dispatched else self.ledger.release)(reservation)
                    if dispatched:
                        self._emit({"type": "api_call", "model": model, "purpose": purpose, "cache": "miss", "attempt": attempt,
                                    "reservation_id": reservation.id, "billing": "uncertain", "error": "cancelled",
                                    "cost_inr": round(reservation.amount, 5), "will_retry": False})
                raise
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
            return {"translation": out, "finish_reason": finish, "truncated": finish == "length", "complete": finish != "length",
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
            finish = choice.get("finish_reason")
            return {"markdown": choice["message"]["content"] or "", "finish_reason": finish, "blocks": blocks, "complete": finish != "length"}, None

        meta, _, info = await self._call(MODEL_OCR, purpose, params, sha256_bytes(image), price_ocr(1), send, parse, lambda m: price_ocr(1))
        return meta, info


def _valid_meta(model: str, meta: dict, blob: bytes | None) -> bool:
    """Cached content must have the shape the model's parser produces."""
    try:
        if model == MODEL_TRANSLATE:
            return isinstance(meta.get("translation"), str)
        if model == MODEL_OCR:
            return isinstance(meta.get("markdown"), str) and isinstance(meta.get("blocks", []), list)
        if model == MODEL_TRANSCRIBE:
            return isinstance(meta.get("text"), str) and isinstance(meta.get("seconds", 0), (int, float))
        if model == MODEL_SPEAK:
            return blob is not None and sniff_audio(blob) is not None
    except AttributeError:
        return False
    return True


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
