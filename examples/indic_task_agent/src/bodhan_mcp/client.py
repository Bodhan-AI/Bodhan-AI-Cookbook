"""The only place that talks to Bodhan over HTTP.

Responsibilities:
* one shared `httpx.AsyncClient`, with a semaphore limiting concurrent requests;
* retries with exponential backoff and jitter on 429, 5xx, timeouts and dropped connections,
  honouring `Retry-After`; other 4xx errors fail fast with a structured error;
* the content-hash cache, plus in-flight de-duplication so two identical concurrent requests
  share one network call;
* charging every request to the caller's `Ledger` (and refusing it if over budget).
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

from .cache import DiskCache, cache_key
from .config import MODEL_KEY_VARS, Settings
from .errors import BodhanToolError
from .usage import CallRecord, Ledger, estimate_translate_tokens, ocr_cost, speak_cost, transcribe_cost, translate_cost

RETRY_STATUSES = {429, 500, 502, 503, 504}


def _error_from_response(resp: httpx.Response, model: str) -> BodhanToolError:
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
    except ValueError:
        err = {}
    message = err.get("message") or resp.text[:300] or resp.reason_phrase
    details = {"status": resp.status_code, "model": model}
    if err.get("code"):
        details["bodhan_code"] = err["code"]
    if err.get("request_id"):
        details["request_id"] = err["request_id"]
    status = resp.status_code
    if status == 401:
        return BodhanToolError(
            "auth_error",
            f"Bodhan rejected the API key for {model}.",
            hint=f"Check {MODEL_KEY_VARS.get(model)} or BODHAN_API_KEY.",
            details=details,
        )
    if status == 413:
        return BodhanToolError(
            "payload_too_large",
            f"{model}: request too large ({message}).",
            hint="Send a smaller input (shorter text, smaller image or audio chunk).",
            details=details,
        )
    if status == 415:
        return BodhanToolError(
            "unsupported_file_type",
            f"{model}: audio container not accepted ({message}).",
            hint="Convert to WAV, FLAC, OGG or MP3.",
            details=details,
        )
    if err.get("code") in {"unsupported_language", "invalid_script", "unsupported_script_for_language"}:
        return BodhanToolError("unsupported_language", f"{model}: {message}", details=details)
    if status == 422:
        return BodhanToolError("invalid_input", f"{model} rejected the input: {message}", details=details)
    if status == 429:
        return BodhanToolError(
            "rate_limited",
            f"{model} is rate limiting requests.",
            retryable=True,
            hint="Wait and retry later, or lower BODHAN_MAX_CONCURRENCY.",
            details=details,
        )
    if status >= 500:
        return BodhanToolError(
            "upstream_error", f"{model} returned {status}: {message}", retryable=True, details=details
        )
    return BodhanToolError("upstream_rejected", f"{model} returned {status}: {message}", details=details)


class BodhanClient:
    def __init__(
        self, settings: Settings, cache: DiskCache | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self.cache = cache or DiskCache(settings.cache_dir, settings.cache_enabled)
        self._sem = asyncio.Semaphore(settings.max_concurrency)
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            transport=transport,
            timeout=httpx.Timeout(settings.http_timeout_s, connect=15.0),
        )
        self._inflight: dict[str, asyncio.Future[bytes]] = {}
        # Translation routes in order of preference; the first that does not 404 is remembered.
        # BODHAN_TRANSLATE_PATH can force one ("chat" = /v1/chat/completions with model indic-translate).
        self._translate_routes = list(dict.fromkeys([settings.translate_path, "chat", "/translate"]))
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep  # swapped out in tests

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport

    async def _post(
        self,
        model: str,
        path: str,
        *,
        timeout: float | None = None,
        retry_statuses: set[int] = RETRY_STATUSES,
        **kwargs: Any,
    ) -> tuple[httpx.Response, int]:
        key = self.settings.key_for(model)
        if not key:
            raise BodhanToolError(
                "missing_api_key",
                f"No API key configured for {model}.",
                hint=f"Set {MODEL_KEY_VARS[model]} or BODHAN_API_KEY in the server's environment.",
            )
        headers = {"Authorization": f"Bearer {key}"}
        last_exc: BodhanToolError | None = None
        attempts = 0
        for attempt in range(self.settings.max_retries + 1):
            attempts = attempt + 1
            try:
                async with self._sem:
                    resp = await self._http.post(
                        path, headers=headers, timeout=timeout or self.settings.http_timeout_s, **kwargs
                    )
            except httpx.TimeoutException:
                last_exc = BodhanToolError(
                    "timeout", f"{model} timed out.", retryable=True, hint="Retry later or raise BODHAN_HTTP_TIMEOUT_S."
                )
                retry_after = None
            except httpx.TransportError as exc:
                last_exc = BodhanToolError(
                    "upstream_error", f"Network error talking to {model}: {exc!s}", retryable=True
                )
                retry_after = None
            else:
                if resp.is_success:
                    return resp, attempts
                err = _error_from_response(resp, model)
                if resp.status_code not in retry_statuses:
                    raise err
                last_exc = err
                retry_after = resp.headers.get("retry-after")
            if attempt == self.settings.max_retries:
                break
            delay = min(30.0, 1.0 * 2**attempt) + random.uniform(0, 1.0)
            if retry_after:
                try:
                    delay = max(delay, min(float(retry_after), 30.0))
                except ValueError:
                    pass
            await self.sleep(delay)
        assert last_exc is not None
        raise last_exc.with_details(attempts=attempts)

    async def _cached(
        self,
        *,
        model: str,
        endpoint: str,
        params: dict[str, Any],
        payload: bytes,
        suffix: str,
        ledger: Ledger,
        estimate: float,
        what: str,
        fetch: Callable[[], Awaitable[tuple[bytes, float, int]]],
        refresh: bool = False,
    ) -> bytes:
        """Serve from cache, join an identical in-flight request, or fetch (charging the ledger)."""
        key = cache_key(model, endpoint, params, payload)
        if not refresh:
            hit = self.cache.get(model, key, suffix)
            if hit is not None:
                ledger.add(CallRecord(model, 0.0, True, 0, 0))
                return hit
            pending = self._inflight.get(key)
            if pending is not None:
                data = await asyncio.shield(pending)
                ledger.add(CallRecord(model, 0.0, True, 0, 0))
                return data
        ledger.reserve(estimate, what)
        fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
        fut.add_done_callback(
            lambda f: f.exception() if not f.cancelled() else None
        )  # never "exception never retrieved"
        self._inflight[key] = fut
        t0 = time.perf_counter()
        try:
            data, cost, attempts = await fetch()
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)
        self.cache.put(model, key, suffix, data)
        ledger.add(CallRecord(model, cost, False, int((time.perf_counter() - t0) * 1000), attempts))
        fut.set_result(data)
        return data

    # ------------------------------------------------------------------ models

    async def transcribe(
        self, audio: bytes, *, seconds: float, language: str | None, ledger: Ledger, filename: str = "chunk.wav"
    ) -> str:
        params = {"language": language or "auto"}
        cost = transcribe_cost(seconds)

        async def fetch() -> tuple[bytes, float, int]:
            data = {"model": "indic-transcribe"}
            if language:
                data["language"] = language
            resp, attempts = await self._post(
                "indic-transcribe",
                "/v1/audio/transcriptions",
                files={"file": (filename, audio, "audio/wav")},
                data=data,
            )
            return json.dumps(resp.json(), ensure_ascii=False).encode(), cost, attempts

        raw = await self._cached(
            model="indic-transcribe",
            endpoint="transcriptions",
            params=params,
            payload=audio,
            suffix=".json",
            ledger=ledger,
            estimate=cost,
            what=f"Transcribing {seconds:.1f}s of audio",
            fetch=fetch,
        )
        return str(json.loads(raw).get("text") or "").strip()

    async def translate(self, text: str, *, target: str, script: str, ledger: Ledger) -> dict[str, Any]:
        """Returns {"translation", "truncated", "usage"} whichever API shape the server speaks.

        The live API (verified Nov 2026) serves translation as OpenAI chat completions with
        model=indic-translate plus a `target_language` field; the Cookbook docs describe a JSON
        POST /translate route. We try the chat form first and fall back to the documented route.
        """
        params = {"target": target, "script": script}

        async def fetch() -> tuple[bytes, float, int]:
            obj, attempts = await self._post_translate(text, target, script)
            tokens = int(
                (obj.get("usage") or {}).get("completion_tokens")
                or estimate_translate_tokens(obj.get("translation", ""))
            )
            return json.dumps(obj, ensure_ascii=False).encode(), translate_cost(tokens), attempts

        raw = await self._cached(
            model="indic-translate",
            endpoint="translate",
            params=params,
            payload=text.encode("utf-8"),
            suffix=".json",
            ledger=ledger,
            estimate=translate_cost(estimate_translate_tokens(text)),
            what=f"Translating {len(text)} characters",
            fetch=fetch,
        )
        return json.loads(raw)

    async def _post_translate(self, text: str, target: str, script: str) -> tuple[dict[str, Any], int]:
        last: BodhanToolError | None = None
        for i, route in enumerate(list(self._translate_routes)):
            try:
                if route == "chat":
                    body: dict[str, Any] = {
                        "model": "indic-translate",
                        "messages": [{"role": "user", "content": text}],
                        "target_language": target,
                    }
                    if script != "native":
                        body["script"] = script
                    resp, attempts = await self._post("indic-translate", "/v1/chat/completions", json=body)
                    obj = resp.json()
                    choice = (obj.get("choices") or [{}])[0]
                    result = {
                        "translation": str((choice.get("message") or {}).get("content") or "").strip(),
                        "truncated": choice.get("finish_reason") == "length",
                        "usage": obj.get("usage") or {},
                        "target_language": target,
                    }
                else:
                    body = {"text": text, "target_language": target}
                    if script != "native":
                        body["script"] = script
                    resp, attempts = await self._post("indic-translate", route, json=body)
                    result = resp.json()
            except BodhanToolError as exc:
                if exc.details.get("status") in {404, 405} and i + 1 < len(self._translate_routes):
                    last = exc
                    continue
                if exc.details.get("status") in {404, 405}:
                    raise exc.with_details(routes_tried=self._translate_routes) from None
                raise
            if i:  # remember the route that worked
                self._translate_routes.insert(0, self._translate_routes.pop(i))
            return result, attempts
        assert last is not None
        raise last

    async def speak(
        self, text: str, *, voice: str, language: str, style: str | None, ledger: Ledger, refresh: bool = False
    ) -> bytes:
        instructions = {"lang": language}
        if style:
            instructions["style"] = style
        params = {"voice": voice, "instructions": instructions}
        cost = speak_cost(len(text))

        async def fetch() -> tuple[bytes, float, int]:
            body = {
                "model": "indic-speak",
                "input": text,
                "voice": voice,
                "instructions": json.dumps(instructions, ensure_ascii=False),
            }
            resp, attempts = await self._post("indic-speak", "/v1/audio/speech", json=body)
            return resp.content, cost, attempts

        return await self._cached(
            model="indic-speak",
            endpoint="speech",
            params=params,
            payload=text.encode("utf-8"),
            suffix=".wav",
            ledger=ledger,
            estimate=cost,
            what=f"Synthesising {len(text)} characters",
            fetch=fetch,
            refresh=refresh,
        )

    async def ocr(
        self, image: bytes, *, mime: str, table_format: str, max_tokens: int | None, ledger: Ledger
    ) -> dict[str, Any]:
        params = {"table_format": table_format, "max_tokens": max_tokens}
        cost = ocr_cost(1)

        async def fetch() -> tuple[bytes, float, int]:
            b64 = base64.b64encode(image).decode()
            body: dict[str, Any] = {
                "model": "indic-ocr",
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}],
                    }
                ],
                "table_format": table_format,
            }
            if max_tokens:
                body["max_tokens"] = max_tokens
            # A 502 from OCR usually means a block overflowed max_tokens: retrying unchanged won't help,
            # so let the OCR tool decide (it retries once with a larger max_tokens).
            resp, attempts = await self._post(
                "indic-ocr",
                "/v1/chat/completions",
                json=body,
                timeout=self.settings.ocr_timeout_s,
                retry_statuses=RETRY_STATUSES - {502},
            )
            obj = resp.json()
            content = ((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            out = {"content": content, "blocks": obj.get("blocks") or []}
            return json.dumps(out, ensure_ascii=False).encode(), cost, attempts

        raw = await self._cached(
            model="indic-ocr",
            endpoint="ocr",
            params=params,
            payload=image,
            suffix=".json",
            ledger=ledger,
            estimate=cost,
            what="OCR of one page",
            fetch=fetch,
        )
        return json.loads(raw)
