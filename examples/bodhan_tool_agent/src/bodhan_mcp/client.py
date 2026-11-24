"""Async client for the four hosted Bodhan models.

One code path handles every request: cache lookup (with in-flight dedupe),
key and budget checks, a per-model concurrency limit, a per-attempt timeout,
and retries with exponential backoff and jitter for 429, 5xx and network
failures (honouring Retry-After). Every request, paid or cached, is metered.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import re
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .cache import DiskCache, cache_key
from .config import (
    MODEL_KEY_ENV,
    PRICE_OCR_PER_IMAGE,
    PRICE_SPEAK_PER_10K_CHARS,
    PRICE_TRANSCRIBE_PER_MIN,
    PRICE_TRANSLATE_PER_10K_OUT_TOKENS,
    Settings,
)
from .errors import ToolFailure

log = logging.getLogger(__name__)

MODEL_IDS = {
    "transcribe": "indic-transcribe",
    "translate": "indic-translate",
    "speak": "indic-speak",
    "ocr": "indic-ocr",
}
MODEL_LABELS = {
    "transcribe": "Indic-Transcribe",
    "translate": "Indic-Translate",
    "speak": "Indic-Speak",
    "ocr": "Indic-OCR",
}

MAX_BACKOFF_S = 30.0
RATE_LIMIT_BACKOFF_BASE_S = 2.0
MAX_RETRY_AFTER_S = 60.0


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def transcribe_cost(duration_s: float) -> float:
    return max(0.0, duration_s) / 60.0 * PRICE_TRANSCRIBE_PER_MIN


def translate_cost(output_tokens: float) -> float:
    return max(0.0, output_tokens) / 10_000 * PRICE_TRANSLATE_PER_10K_OUT_TOKENS


def speak_cost(chars: int) -> float:
    return max(0, chars) / 10_000 * PRICE_SPEAK_PER_10K_CHARS


def ocr_cost(images: int = 1) -> float:
    return max(0, images) * PRICE_OCR_PER_IMAGE


def estimate_translate_tokens(text: str) -> int:
    # Indic scripts tokenize to roughly one token per character or two; erring
    # high keeps the budget check conservative, and the amounts are tiny anyway.
    return max(16, int(len(text) * 1.2))


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------

class CostMeter:
    def __init__(self, limit_inr: float | None = None) -> None:
        self.limit_inr = limit_inr
        self.spent_inr = 0.0
        self.records: list[dict[str, Any]] = []
        self._reserved = 0.0

    @property
    def remaining_inr(self) -> float | None:
        if self.limit_inr is None:
            return None
        return max(0.0, self.limit_inr - self.spent_inr - self._reserved)

    def check(self, estimate_inr: float, what: str = "this call") -> None:
        if self.limit_inr is None:
            return
        if self.spent_inr + self._reserved + estimate_inr > self.limit_inr + 1e-9:
            raise ToolFailure(
                "budget_exceeded",
                f"{what} would cost about INR {estimate_inr:.4f}, but only INR "
                f"{max(0.0, self.limit_inr - self.spent_inr - self._reserved):.4f} of the "
                f"INR {self.limit_inr:.2f} budget is left (spent INR {self.spent_inr:.4f}).",
                "Reduce the work (fewer pages, shorter text or audio) or raise the budget "
                "(BODHAN_MAX_COST_INR). Results already produced are cached and cost nothing to fetch again.",
                details={"estimate_inr": round(estimate_inr, 4), "spent_inr": round(self.spent_inr, 4),
                         "limit_inr": self.limit_inr},
            )

    def reserve(self, amount: float) -> None:
        self._reserved += amount

    def release(self, amount: float) -> None:
        self._reserved = max(0.0, self._reserved - amount)

    def record(self, model: str, cost_inr: float, cache_hit: bool, latency_s: float,
               request_id: str | None, units: float) -> None:
        self.spent_inr += cost_inr
        self.records.append({
            "model": model,
            "cost_inr": round(cost_inr, 6),
            "cache_hit": cache_hit,
            "latency_s": round(latency_s, 3),
            "request_id": request_id,
            "units": units,
        })


@dataclass
class Usage:
    """What one tool invocation consumed. Collected through a context variable so
    helpers deep in a tool don't need to thread it through every call."""

    cost_inr: float = 0.0
    calls: int = 0
    cache_hits: int = 0
    request_ids: list[str] = field(default_factory=list)


_current_usage: ContextVar[Usage | None] = ContextVar("bodhan_usage", default=None)


@contextmanager
def track_usage() -> Iterator[Usage]:
    usage = Usage()
    token = _current_usage.set(usage)
    try:
        yield usage
    finally:
        _current_usage.reset(token)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

@dataclass
class _Request:
    kind: str
    path: str
    key: str
    estimate_inr: float
    estimate_units: float
    json_body: dict[str, Any] | None = None
    form: dict[str, str] | None = None
    files: dict[str, tuple[str, bytes, str]] | None = None
    binary_response: bool = False
    # Statuses the caller handles itself (e.g. OCR 502 means "raise max_tokens"), so no blind retry.
    no_retry_status: frozenset[int] = frozenset()
    cost_of: Callable[[bytes], tuple[float, float]] | None = None


def audio_format(data: bytes) -> str | None:
    """'wav' or 'mp3' by magic bytes. The speech route is documented as WAV but serves MP3."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    return None


_UNSUPPORTED_LANG = re.compile(r"unsupported (target_)?language|language .{0,40}not supported", re.I)


def _error_envelope(resp: httpx.Response) -> tuple[str | None, str]:
    """Return (api error code, message) from Bodhan's error envelope or a FastAPI-style detail."""
    try:
        body = resp.json()
    except (ValueError, UnicodeDecodeError):
        return None, resp.text[:300]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            # LiteLLM-style envelopes put the HTTP status in `code` and the symbol in `type`.
            code = err.get("code")
            if code is None or str(code).isdigit():
                code = err.get("type")
            return (str(code) if code is not None else None), str(err.get("message", ""))[:500]
        if "detail" in body:
            detail = body["detail"]
            return None, (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))[:500]
    return None, json.dumps(body, ensure_ascii=False)[:300]


def _retry_after_s(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return min(MAX_RETRY_AFTER_S, max(0.0, float(value)))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        return min(MAX_RETRY_AFTER_S, max(0.0, when.timestamp() - time.time()))
    except (TypeError, ValueError):
        return None


class _LoopState:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None) -> None:
        self.http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.call_timeout_s, connect=min(20.0, settings.call_timeout_s)),
            headers={"User-Agent": "bodhan-mcp/0.1"},
        )
        self.semaphores = {
            kind: asyncio.Semaphore(settings.model_concurrency.get(kind, settings.concurrency)) for kind in MODEL_IDS
        }


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: DiskCache | None = None,
        meter: CostMeter | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.cache = cache or DiskCache(settings.cache_dir, settings.cache_enabled)
        self.meter = meter or CostMeter(settings.max_cost_inr)
        self._auth_ok: set[str] = set()
        # httpx clients and asyncio primitives belong to one event loop; tests and
        # embedding callers may drive the same client from several loops.
        self._loops: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopState] = weakref.WeakKeyDictionary()
        self.sleep: Callable[[float], Any] = asyncio.sleep

    def _state(self) -> _LoopState:
        loop = asyncio.get_running_loop()
        state = self._loops.get(loop)
        if state is None:
            state = _LoopState(self.settings, self.transport)
            self._loops[loop] = state
        return state

    async def aclose(self) -> None:
        loop = asyncio.get_running_loop()
        state = self._loops.pop(loop, None)
        if state is not None:
            await state.http.aclose()

    # -- public model calls -------------------------------------------------

    def transcribe_request_key(self, audio: bytes, language: str | None) -> str:
        return cache_key("transcribe", {"model": MODEL_IDS["transcribe"], "language": language}, audio)

    async def transcribe(self, audio: bytes, *, duration_s: float, language: str | None = None,
                         filename: str = "audio.wav", mime: str = "audio/wav") -> str:
        """Transcribe one clip of at most 30 s. Returns the transcript text."""
        form = {"model": MODEL_IDS["transcribe"]}
        if language:
            form["language"] = language
        cost = transcribe_cost(duration_s)
        req = _Request(
            kind="transcribe",
            path="/v1/audio/transcriptions",
            key=self.transcribe_request_key(audio, language),
            estimate_inr=cost,
            estimate_units=round(duration_s, 3),
            form=form,
            files={"file": (filename, audio, mime)},
            cost_of=lambda _body: (cost, round(duration_s, 3)),
        )
        body = json.loads(await self._call(req))
        text = body.get("text")
        if not isinstance(text, str):
            raise ToolFailure("upstream_error", "Indic-Transcribe returned no 'text' field.",
                              "Retry later; if it persists the API response format may have changed.",
                              retryable=True)
        return text

    def translate_request_key(self, text: str, target_language: str, script: str = "native",
                              max_output_tokens: int | None = None) -> str:
        return cache_key("translate", self._translate_body(text, target_language, script, max_output_tokens))

    @staticmethod
    def _translate_body(text: str, target_language: str, script: str, max_output_tokens: int | None) -> dict:
        # The hosted gateway serves translation on the chat-completions route: exactly one user
        # turn carrying the raw text (a system turn is rejected), with the target as extra fields.
        body: dict[str, Any] = {
            "model": MODEL_IDS["translate"],
            "messages": [{"role": "user", "content": text}],
            "target_language": target_language,
            "script": script,
        }
        if max_output_tokens:
            body["max_output_tokens"] = int(max_output_tokens)
        return body

    @staticmethod
    def _normalize_translation(data: dict[str, Any], target_language: str, script: str) -> dict[str, Any]:
        """Give callers one shape: {translation, truncated, target_language, script, usage}."""
        if isinstance(data.get("translation"), str):
            return data
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ToolFailure("upstream_error", "Indic-Translate returned no translation.", "Retry later.",
                              retryable=True)
        choice = choices[0]
        content = (choice.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ToolFailure("upstream_error", "Indic-Translate returned no translation text.", "Retry later.",
                              retryable=True)
        return {
            "translation": content,
            "truncated": bool(data.get("truncated")) or choice.get("finish_reason") == "length",
            "target_language": data.get("target_language", target_language),
            "script": data.get("script", script),
            "usage": data.get("usage") or {},
        }

    async def translate(self, text: str, target_language: str, script: str = "native",
                        max_output_tokens: int | None = None) -> dict[str, Any]:
        """Translate one chunk. Returns {translation, truncated, target_language, script, usage}."""
        if not text.strip():
            raise ToolFailure("invalid_argument", "Nothing to translate: the text is empty.", "Pass non-empty text.")
        body = self._translate_body(text, target_language, script, max_output_tokens)
        est_tokens = estimate_translate_tokens(text)

        def cost_of(raw: bytes) -> tuple[float, float]:
            data = self._normalize_translation(json.loads(raw), target_language, script)
            tokens = (data.get("usage") or {}).get("completion_tokens")
            if not isinstance(tokens, (int, float)):
                tokens = estimate_translate_tokens(str(data.get("translation", "")))
            return translate_cost(tokens), tokens

        req = _Request(
            kind="translate",
            path="/v1/chat/completions",
            key=cache_key("translate", body),
            estimate_inr=translate_cost(est_tokens),
            estimate_units=est_tokens,
            json_body=body,
            cost_of=cost_of,
        )
        return self._normalize_translation(json.loads(await self._call(req)), target_language, script)

    def speak_request_key(self, text: str, voice: str, lang: str, style: str | None = None) -> str:
        return cache_key("speak", self._speak_body(text, voice, lang, style))

    @staticmethod
    def _speak_body(text: str, voice: str, lang: str, style: str | None) -> dict[str, Any]:
        instructions: dict[str, str] = {"lang": lang}
        if style:
            instructions["style"] = style
        return {
            "model": MODEL_IDS["speak"],
            "input": text,
            "voice": voice,
            "instructions": json.dumps(instructions, ensure_ascii=False),
        }

    async def speak(self, text: str, *, voice: str, lang: str, style: str | None = None) -> bytes:
        """Synthesize one short text (a sentence or two). Returns encoded audio (WAV or MP3)."""
        if not text.strip():
            raise ToolFailure("invalid_argument", "Nothing to synthesize: the text is empty.",
                              "Pass non-empty text.")
        body = self._speak_body(text, voice, lang, style)
        cost = speak_cost(len(text))
        req = _Request(
            kind="speak",
            path="/v1/audio/speech",
            key=cache_key("speak", body),
            estimate_inr=cost,
            estimate_units=len(text),
            json_body=body,
            binary_response=True,
            cost_of=lambda _raw: (cost, len(text)),
        )
        return await self._call(req)

    def ocr_request_key(self, image: bytes, *, mime: str, table_format: str, max_tokens: int) -> str:
        return cache_key("ocr", {"model": MODEL_IDS["ocr"], "table_format": table_format,
                                 "max_tokens": int(max_tokens), "mime": mime}, image)

    async def ocr(self, image: bytes, *, mime: str = "image/png", table_format: str = "markdown",
                  max_tokens: int = 2048) -> dict[str, Any]:
        """OCR one page image (PNG or JPEG). Returns the chat.completion body including `blocks`.

        A 502 is raised immediately (as upstream_error with details.status=502) because it
        usually means a block exceeded max_tokens; the caller decides whether to raise it.
        """
        data_uri = f"data:{mime};base64," + base64.b64encode(image).decode()
        body = {
            "model": MODEL_IDS["ocr"],
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": data_uri}}]}],
            "table_format": table_format,
            "max_tokens": int(max_tokens),
        }
        req = _Request(
            kind="ocr",
            path="/v1/chat/completions",
            key=self.ocr_request_key(image, mime=mime, table_format=table_format, max_tokens=max_tokens),
            estimate_inr=ocr_cost(1),
            estimate_units=1,
            json_body=body,
            no_retry_status=frozenset({502}),
            cost_of=lambda _raw: (ocr_cost(1), 1),
        )
        data = json.loads(await self._call(req))
        if not data.get("choices") and not data.get("blocks"):
            raise ToolFailure("upstream_error", "Indic-OCR returned neither content nor blocks.",
                              "Retry later.", retryable=True)
        return data

    # -- machinery ----------------------------------------------------------

    async def _call(self, req: _Request) -> bytes:
        usage = _current_usage.get()
        started = time.monotonic()
        model = MODEL_IDS[req.kind]

        async def compute() -> bytes:
            return await self._send(req, usage)

        raw, hit = await self.cache.get_or_compute(req.key, compute)
        if hit:
            self.meter.record(model, 0.0, True, time.monotonic() - started, None, req.estimate_units)
            if usage is not None:
                usage.cache_hits += 1
        return raw

    def _api_key(self, kind: str) -> str:
        key = (self.settings.keys or {}).get(kind)
        if not key:
            env = MODEL_KEY_ENV[kind]
            raise ToolFailure(
                "missing_api_key",
                f"No API key configured for {MODEL_LABELS[kind]}.",
                f"Set {env} (or BODHAN_API_KEY) in the server's environment; keys are issued per model "
                "at https://console.bodhan.ai/.",
            )
        return key

    async def _send(self, req: _Request, usage: Usage | None) -> bytes:
        api_key = self._api_key(req.kind)
        self.meter.check(req.estimate_inr, f"This {MODEL_LABELS[req.kind]} request")
        self.meter.reserve(req.estimate_inr)
        try:
            return await self._send_with_retries(req, api_key, usage)
        finally:
            self.meter.release(req.estimate_inr)

    async def _send_with_retries(self, req: _Request, api_key: str, usage: Usage | None) -> bytes:
        state = self._state()
        url = self.settings.base_url.rstrip("/") + req.path
        headers = {"Authorization": f"Bearer {api_key}"}
        attempts = max(0, self.settings.max_retries) + 1
        auth_retried = False
        last: ToolFailure | None = None

        for attempt in range(attempts):
            delay: float | None = None
            started = time.monotonic()
            try:
                async with state.semaphores[req.kind]:
                    resp = await state.http.post(url, headers=headers, json=req.json_body,
                                                 data=req.form, files=req.files)
            except httpx.TimeoutException:
                last = ToolFailure(
                    "timeout",
                    f"{MODEL_LABELS[req.kind]} did not answer within {self.settings.call_timeout_s:.0f} s.",
                    "Retry later or raise BODHAN_CALL_TIMEOUT_S.",
                    retryable=True,
                )
            except httpx.TransportError as exc:
                last = ToolFailure(
                    "upstream_error",
                    f"Could not reach {MODEL_LABELS[req.kind]}: {type(exc).__name__}.",
                    f"Check network access to {self.settings.base_url} and retry.",
                    retryable=True,
                )
            else:
                latency = time.monotonic() - started
                request_id = resp.headers.get("x-request-id")
                if resp.status_code == 200:
                    raw = self._validate_success(req, resp)
                    self._auth_ok.add(req.kind)
                    cost, units = req.cost_of(raw) if req.cost_of else (req.estimate_inr, req.estimate_units)
                    self.meter.record(MODEL_IDS[req.kind], cost, False, latency, request_id, units)
                    if usage is not None:
                        usage.calls += 1
                        usage.cost_inr += cost
                        if request_id:
                            usage.request_ids.append(request_id)
                    return raw
                last = self._map_error(req, resp)
                if resp.status_code in req.no_retry_status:
                    raise last
                if last.code == "auth_error" and req.kind in self._auth_ok and not auth_retried:
                    # The docs advise one retry when the same key worked moments ago.
                    auth_retried = True
                    last.retryable = True
                if not last.retryable:
                    raise last
                delay = _retry_after_s(resp)

            if attempt + 1 >= attempts:
                break
            if delay is None:
                # The hosted API sends no Retry-After on 429, and a sub-second start gives up before its
                # throttle window clears, so rate limits back off from a larger base than transient errors.
                base = RATE_LIMIT_BACKOFF_BASE_S if last.code == "rate_limited" else 0.5
                delay = min(MAX_BACKOFF_S, base * (2 ** attempt)) * (0.5 + random.random())
            log.info("%s attempt %d failed (%s); retrying in %.1fs", req.kind, attempt + 1, last.code, delay)
            await self.sleep(delay)

        assert last is not None
        last.message += f" (gave up after {attempts} attempts)"
        raise last

    def _validate_success(self, req: _Request, resp: httpx.Response) -> bytes:
        raw = resp.content
        if req.binary_response:
            if audio_format(raw) is None:
                raise ToolFailure("upstream_error",
                                  f"{MODEL_LABELS[req.kind]} returned {len(raw)} bytes that are not audio.",
                                  "Retry later.", retryable=True)
            return raw
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ToolFailure("upstream_error", f"{MODEL_LABELS[req.kind]} returned a non-JSON response.",
                              "Retry later.", retryable=True) from exc
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            # Some gateways answer 200 with an error envelope; treat it like the status it names.
            err = data["error"]
            raise self._failure_for(req, 400, err.get("code"), str(err.get("message", "")), None)
        return raw

    def _map_error(self, req: _Request, resp: httpx.Response) -> ToolFailure:
        api_code, message = _error_envelope(resp)
        return self._failure_for(req, resp.status_code, api_code, message, resp.headers.get("x-request-id"))

    def _failure_for(self, req: _Request, status: int, api_code: str | None, message: str,
                     request_id: str | None) -> ToolFailure:
        label = MODEL_LABELS[req.kind]
        details: dict[str, Any] = {"status": status, "api_code": api_code, "request_id": request_id}
        text = f"{label} rejected the request (HTTP {status}{', ' + api_code if api_code else ''}): {message}"
        if status in (401, 403):
            return ToolFailure("auth_error", text,
                               f"Check that {MODEL_KEY_ENV[req.kind]} holds a valid, funded key for {label}.",
                               details=details)
        if status == 429:
            return ToolFailure("rate_limited", text, "The tool retries automatically; wait a minute and retry.",
                               retryable=True, details=details)
        if api_code == "unsupported_language" or (status == 400 and _UNSUPPORTED_LANG.search(message or "")):
            return ToolFailure("unsupported_language", text,
                               "Call list_languages_and_voices for the codes each model accepts.", details=details)
        if status == 415:
            return ToolFailure("unsupported_file_type", text, "Send WAV, FLAC, OGG or MP3 audio.", details=details)
        if status == 413:
            return ToolFailure("invalid_argument", text, "The input is too large for one request; send less.",
                               details=details)
        if status in (400, 422):
            return ToolFailure("invalid_argument", text, "Fix the named argument and retry.", details=details)
        if status == 404:
            return ToolFailure("upstream_error", text, "Check BODHAN_BASE_URL (expected https://api.bodhan.ai).",
                               details=details)
        if status >= 500:
            return ToolFailure("upstream_error", text, "Temporary server problem; retry later.",
                               retryable=True, details=details)
        return ToolFailure("upstream_error", text, "Unexpected response; retry later.", details=details)
