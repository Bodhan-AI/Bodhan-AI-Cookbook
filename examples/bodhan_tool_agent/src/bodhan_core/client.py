"""Async HTTP client for the four Bodhan models.

One request = one model call at the API's native granularity (one audio window, one text chunk,
one TTS sentence group, one page). Chunking is the tools' job; this layer adds:

* per-model keys (``BODHAN_<MODEL>_KEY``), never logged
* a global concurrency limit (``asyncio.Semaphore``)
* per-call timeouts
* retries with exponential backoff + jitter on 429 / 5xx / timeouts, honouring ``Retry-After``;
  a 429 that means "out of credit" is *not* retried
* a content-addressed cache, so the same input is never sent to the same model twice
* a cost ledger with a pre-call budget check
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from .cache import Cache, cache_key
from .config import MODEL_KEY_ENV, Settings
from .errors import ErrorCode, ToolError
from .ledger import Ledger, cost_ocr, cost_speak, cost_transcribe, cost_translate, estimate_tokens

QUOTA_WORDS = ("credit", "quota", "balance", "budget", "insufficient", "billing")
MAX_RETRY_AFTER_S = 30.0


@dataclass
class CallResult:
    model: str
    data: dict | None  # parsed JSON response (None for audio)
    content: bytes | None  # raw bytes for audio responses
    cached: bool
    cost_inr: float
    latency_ms: int
    attempts: int
    request_id: str | None = None


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        *,
        ledger: Ledger | None = None,
        cache: Cache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.settings = settings
        self.ledger = ledger or Ledger()
        self.cache = cache if cache is not None else Cache(settings.cache_path)
        self._sem = asyncio.Semaphore(settings.max_concurrency)
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=settings.bodhan_base_url,
            timeout=httpx.Timeout(settings.call_timeout_s, connect=10.0),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> BodhanClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ public model calls

    async def transcribe(
        self, audio: bytes, *, filename: str, duration_s: float, language: str | None = None, **extra
    ) -> CallResult:
        """One audio window (keep it ≤ 30 s). ``duration_s`` drives the cost estimate."""
        params = {"model": "indic-transcribe", **({"language": language} if language else {}), **extra}
        form = {k: (json.dumps(v) if isinstance(v, (list, dict)) else str(v)) for k, v in params.items()}

        def build() -> dict:
            return {"files": {"file": (filename, audio, _audio_mime(filename))}, "data": form}

        return await self._call(
            "indic-transcribe", "POST", "/v1/audio/transcriptions", build,
            key_params=params, payload=audio, estimate=cost_transcribe(duration_s),
            actual=lambda data, _c: cost_transcribe(duration_s),
        )

    async def translate(
        self, text: str, *, target_language: str, script: str | None = None, **extra
    ) -> CallResult:
        """One text chunk. The source language is inferred by the API (it accepts no source field).

        Sent over the documented chat-completions transport (``/translate`` returns 404 for the keys
        we tested; the docs say both give identical output). The response is normalised to the
        ``/translate`` shape: ``{"translation", "target_language", "script", "truncated", "usage"}``.
        """
        opts = {"target_language": target_language, **({"script": script} if script else {}), **extra}
        body = {"model": "indic-translate", "messages": [{"role": "user", "content": text}], **opts}

        def actual(data: dict | None, _c) -> float:
            usage = (data or {}).get("usage") or {}
            tokens = usage.get("completion_tokens") or usage.get("output_tokens")
            if not tokens:
                tokens = estimate_tokens((data or {}).get("translation", ""))
            return cost_translate(int(tokens))

        result = await self._call(
            "indic-translate", "POST", "/v1/chat/completions", lambda: {"json": body},
            key_params={"text": text, **opts}, estimate=cost_translate(estimate_tokens(text) * 2), actual=actual,
            normalize=lambda d: {**_normalize_translation(d), "target_language": target_language,
                                 "script": script or "native"},
        )
        return result

    async def speak(
        self, text: str, *, voice: str, lang: str, style: str | None = None, attempt: int = 0, **extra
    ) -> CallResult:
        """One short TTS input (a sentence or two). Returns WAV bytes in ``content``.

        ``attempt`` > 0 is a deliberate re-synthesis after failed verification: it changes the cache
        key so the retry is explicit, but it is not sent to the API.
        """
        instructions = {"lang": lang, **({"style": style} if style else {})}
        body = {"model": "indic-speak", "input": text, "voice": voice, "instructions": json.dumps(instructions), **extra}
        return await self._call(
            "indic-speak", "POST", "/v1/audio/speech", lambda: {"json": body},
            key_params={**body, "attempt": attempt}, estimate=cost_speak(len(text)),
            actual=lambda _d, _c: cost_speak(len(text)), binary=True,
        )

    async def ocr(self, image: bytes, *, mime: str = "image/png", table_format: str = "markdown", **extra) -> CallResult:
        """One page image (PNG/JPEG). The base64 data URL exists only inside this HTTP request."""
        params = {"model": "indic-ocr", "table_format": table_format, **extra}

        def build() -> dict:
            url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
            return {"json": {**params, "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": url}}]}]}}

        return await self._call(
            "indic-ocr", "POST", "/v1/chat/completions", build,
            key_params=params, payload=image, estimate=cost_ocr(1), actual=lambda _d, _c: cost_ocr(1),
        )

    # ------------------------------------------------------------------ core request path

    async def _call(
        self,
        model: str,
        method: str,
        path: str,
        build: Callable[[], dict],
        *,
        key_params: dict,
        estimate: float,
        actual: Callable[[dict | None, bytes | None], float],
        payload: bytes = b"",
        binary: bool = False,
        normalize: Callable[[dict], dict] | None = None,
    ) -> CallResult:
        key = cache_key(model, key_params, payload)
        hit = self.cache.get(key)
        if hit is not None:
            data, content = hit
            self.ledger.record(model, 0.0, cached=True, path=path)
            return CallResult(model, data, content, cached=True, cost_inr=0.0, latency_ms=0, attempts=0)

        api_key = self.settings.key_for(model)
        if not api_key:
            raise ToolError(
                ErrorCode.MISSING_API_KEY,
                f"No API key configured for {model}.",
                hint=f"Set {MODEL_KEY_ENV[model]} (or BODHAN_API_KEY) in the environment or .env.",
            )
        self.ledger.check(model, estimate)

        started = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                async with self._sem:
                    resp = await self._http.request(
                        method, path, headers={"Authorization": f"Bearer {api_key}"}, **build()
                    )
                result = self._parse(model, resp, binary)
                break
            except _Retry as r:
                if attempts > self.settings.max_retries:
                    raise r.error from None
                await self._sleep(r.retry_after if r.retry_after is not None else _backoff(attempts))
            except httpx.TimeoutException:
                if attempts > self.settings.max_retries:
                    raise ToolError(
                        ErrorCode.UPSTREAM_TIMEOUT,
                        f"{model} did not respond within {self.settings.call_timeout_s:.0f}s "
                        f"({attempts} attempts).",
                        hint="Retry later, or send a smaller input.",
                    ) from None
                await self._sleep(_backoff(attempts))
            except httpx.TransportError as e:
                if attempts > self.settings.max_retries:
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR, f"Could not reach {model}: {type(e).__name__}.",
                        hint="Check network access to the Bodhan API.",
                    ) from None
                await self._sleep(_backoff(attempts))

        data, content, request_id = result
        if normalize and data is not None:
            data = normalize(data)
        cost = actual(data, content)
        self.cache.put(key, model, data, content)
        self.ledger.record(model, cost, cached=False, path=path, request_id=request_id)
        return CallResult(
            model, data, content, cached=False, cost_inr=cost,
            latency_ms=int((time.monotonic() - started) * 1000), attempts=attempts, request_id=request_id,
        )

    def _parse(self, model: str, resp: httpx.Response, binary: bool) -> tuple[dict | None, bytes | None, str | None]:
        request_id = resp.headers.get("x-request-id")
        if resp.is_success:
            if binary:
                if resp.headers.get("content-type", "").startswith("application/json"):
                    raise ToolError(ErrorCode.UPSTREAM_ERROR, f"{model} returned JSON instead of audio: {resp.text[:200]}")
                return None, resp.content, request_id
            try:
                return resp.json(), None, request_id
            except ValueError:
                raise ToolError(ErrorCode.UPSTREAM_ERROR, f"{model} returned non-JSON: {resp.text[:200]}") from None

        message, code, rid = _error_body(resp)
        request_id = rid or request_id
        where = f" (request_id={request_id})" if request_id else ""
        detail = f"{model} HTTP {resp.status_code}{f' {code}' if code else ''}: {message}{where}"
        status = resp.status_code
        if status == 429:
            if any(w in f"{code} {message}".lower() for w in QUOTA_WORDS):
                raise ToolError(ErrorCode.QUOTA_EXHAUSTED, detail, hint="Top up Bodhan credit; retrying will not help.")
            raise _Retry(ToolError(ErrorCode.RATE_LIMITED, detail, hint="Rate limited; back off and retry."),
                         _retry_after(resp))
        if status >= 500:
            raise _Retry(ToolError(ErrorCode.UPSTREAM_ERROR, detail, hint="Bodhan server error; retry later."),
                         _retry_after(resp))
        if "unsupported language" in message.lower() or code == "unsupported_language":
            raise ToolError(ErrorCode.UNSUPPORTED_LANGUAGE, detail, hint="Call supported_languages for valid codes.")
        if status in (401, 403):
            raise ToolError(ErrorCode.AUTH_FAILED, detail, hint=f"Check {MODEL_KEY_ENV[model]} is a valid key for {model}.")
        if status == 413:
            raise ToolError(ErrorCode.INVALID_ARGUMENT, detail, hint="Input too large for one request; send smaller chunks.")
        raise ToolError(ErrorCode.INVALID_ARGUMENT, detail, hint="Fix the request arguments; retrying unchanged will fail.")


def _normalize_translation(data: dict) -> dict:
    """Chat-completions response -> the documented /translate response shape."""
    if "translation" in data:
        return data
    choice = (data.get("choices") or [{}])[0]
    return {
        "translation": (choice.get("message") or {}).get("content") or "",
        "target_language": data.get("target_language"),
        "script": data.get("script"),
        "truncated": choice.get("finish_reason") == "length",
        "usage": data.get("usage"),
    }


class _Retry(Exception):
    def __init__(self, error: ToolError, retry_after: float | None):
        self.error = error
        self.retry_after = retry_after


def _backoff(attempt: int) -> float:
    return min(2 ** (attempt - 1), 16) + random.uniform(0, 0.5)


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return min(float(value), MAX_RETRY_AFTER_S) if value else None
    except ValueError:
        return None


def _error_body(resp: httpx.Response) -> tuple[str, str | None, str | None]:
    """Bodhan errors are {"error": {"message", "code", "request_id"}}."""
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            return str(err.get("message") or resp.reason_phrase), err.get("code"), err.get("request_id")
        return str(err), None, None
    except (ValueError, AttributeError):
        return resp.text[:200] or resp.reason_phrase, None, None


def _audio_mime(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    return {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "ogg": "audio/ogg",
            "m4a": "audio/mp4", "webm": "audio/webm"}.get(ext, "application/octet-stream")
