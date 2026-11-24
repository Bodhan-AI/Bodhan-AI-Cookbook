"""The single door to the Bodhan hosted API.

Every speech-recognition, translation, speech-synthesis and OCR request in this project
goes through BodhanClient.request(). Keeping one door makes the "only Bodhan models"
rule auditable, and gives retries, timeouts and the concurrency limit one home.

Endpoint paths are configurable per model; this client owns the request shapes and
normalizes the provider responses consumed by the per-model tools.

Behaviour summary
- One API key per model, read from the environment at call time (never logged).
- At most `max_concurrency` requests in flight; the slot is held only while a request
  is on the wire, not while backing off.
- Retries on 408/429/5xx, timeouts and network errors, with exponential backoff and
  jitter. A Retry-After header is honoured (capped).
- Out-of-credit responses (often a 429 in OpenAI-style APIs) are NOT retried.
- Two clocks: `attempt_timeout_s` per request, and `total_timeout_s` as a hard ceiling
  on the whole call including retries and sleeping.
- Every failure leaves as a ToolError with a code, a retryable flag and a hint.
"""

from __future__ import annotations

import asyncio
import base64
import json as jsonlib
import mimetypes
import os
import random
import threading
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from .models import ErrorCode, ToolError, Usage

DEFAULT_BASE_URL = "https://api.bodhan.ai/v1"

ModelName = Literal["transcribe", "translate", "speak", "ocr"]

KEY_ENV: dict[str, str] = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}

ENDPOINT_ENV: dict[str, str] = {
    "transcribe": "BODHAN_TRANSCRIBE_ENDPOINT",
    "translate": "BODHAN_TRANSLATE_ENDPOINT",
    "speak": "BODHAN_SPEAK_ENDPOINT",
    "ocr": "BODHAN_OCR_ENDPOINT",
}

DEFAULT_ENDPOINTS: dict[str, str] = {
    "transcribe": "/audio/transcriptions",
    "translate": "/chat/completions",
    "speak": "/audio/speech",
    "ocr": "/chat/completions",
}
DEFAULT_SPEAK_VOICE = "Amit"

# Statuses worth repeating. 429 is handled separately because it can mean "no credit".
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_QUOTA_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "insufficient credit",
    "out of credit",
    "credit exhausted",
    "quota exceeded",
    "billing",
)
_MAX_ERROR_TEXT = 300


# --------------------------------------------------------------------------- config


def _env_number(env: Mapping[str, str], name: str, default: float, cast: type) -> Any:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ValueError(f"{name} must be a {cast.__name__}, got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {raw!r}")
    return value


@dataclass(frozen=True)
class ClientConfig:
    base_url: str = DEFAULT_BASE_URL
    max_concurrency: int = 4
    max_retries: int = 4  # retries after the first attempt, so up to 5 attempts
    attempt_timeout_s: float = 60.0
    total_timeout_s: float = 180.0
    backoff_base_s: float = 1.0
    backoff_cap_s: float = 20.0
    retry_after_cap_s: float = 30.0

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if self.attempt_timeout_s <= 0 or self.total_timeout_s <= 0:
            raise ValueError("timeouts must be positive")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ClientConfig:
        env = os.environ if env is None else env
        d = cls()
        return cls(
            base_url=(env.get("BODHAN_BASE_URL") or d.base_url).rstrip("/"),
            max_concurrency=_env_number(env, "BODHAN_MAX_CONCURRENCY", d.max_concurrency, int),
            max_retries=_env_number(env, "BODHAN_MAX_RETRIES", d.max_retries, int),
            attempt_timeout_s=_env_number(
                env, "BODHAN_ATTEMPT_TIMEOUT_S", d.attempt_timeout_s, float
            ),
            total_timeout_s=_env_number(env, "BODHAN_TOTAL_TIMEOUT_S", d.total_timeout_s, float),
            backoff_base_s=_env_number(env, "BODHAN_BACKOFF_BASE_S", d.backoff_base_s, float),
            backoff_cap_s=_env_number(env, "BODHAN_BACKOFF_CAP_S", d.backoff_cap_s, float),
            retry_after_cap_s=_env_number(
                env, "BODHAN_RETRY_AFTER_CAP_S", d.retry_after_cap_s, float
            ),
        )


# ------------------------------------------------------------------------- response


@dataclass
class BodhanResponse:
    status: int
    headers: Mapping[str, str]
    content: bytes
    latency_ms: int  # wall time for the whole call, including retries and backoff
    attempts: int

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return jsonlib.loads(self.content)
        except ValueError:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Bodhan returned a response that is not valid JSON.",
                retryable=False,
                hint="If this repeats, the endpoint path or response format may differ from "
                "what the tool expects; check the Bodhan API docs.",
                details={"status": self.status, "body_start": self.text[:120]},
            ) from None


@dataclass
class ClientStats:
    requests: int = 0  # HTTP requests actually sent (every attempt counts)
    retries: int = 0
    calls: Counter = field(default_factory=Counter)  # successful calls per model
    failures: Counter = field(default_factory=Counter)  # failed calls per model


# --------------------------------------------------------------------------- client


class BodhanClient:
    """Reusable synchronous and asynchronous client for the Bodhan API."""

    def __init__(
        self,
        config: ClientConfig | None = None,
        *,
        env: Mapping[str, str] | None = None,
        transport: Any = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._env: Mapping[str, str] = os.environ if env is None else env
        self.config = config or ClientConfig.from_env(self._env)
        self._transport = transport  # tests inject httpx.MockTransport here
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()
        self._clock = clock or time.monotonic
        self._sem = asyncio.Semaphore(self.config.max_concurrency)
        self._http: httpx.AsyncClient | None = None
        self._sync_sem = threading.BoundedSemaphore(self.config.max_concurrency)
        self._sync_client_lock = threading.Lock()
        self._sync_http: httpx.Client | None = None
        self.stats = ClientStats()

    # -- lifecycle

    async def __aenter__(self) -> BodhanClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self.close()

    def close(self) -> None:
        if self._sync_http is not None:
            self._sync_http.close()
            self._sync_http = None

    def __enter__(self) -> BodhanClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(transport=self._transport, follow_redirects=False)
        return self._http

    def _url(self, path: str) -> str:
        parsed = urlsplit(path)
        if parsed.scheme:
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ToolError(
                    ErrorCode.INVALID_ARGUMENT,
                    "Bodhan endpoint must be an http(s) URL or a path relative to BODHAN_BASE_URL.",
                )
            return path
        return f"{self.config.base_url}/{path.lstrip('/')}"

    # -- keys

    def api_key(self, model: ModelName) -> str:
        env_name = KEY_ENV.get(model)
        if env_name is None:
            raise ToolError(ErrorCode.INTERNAL, f"Unknown Bodhan model {model!r}.")
        key = (self._env.get(env_name) or "").strip()
        if not key:
            raise ToolError(
                ErrorCode.AUTH_MISSING,
                f"No API key configured for the {model} model.",
                retryable=False,
                hint=f"Set {env_name} in the environment of the MCP server "
                f"(see .env.example). Keys are issued per model in the Bodhan console.",
            )
        return key

    def has_key(self, model: ModelName) -> bool:
        return bool((self._env.get(KEY_ENV[model]) or "").strip())

    # -- requests

    async def request(
        self,
        model: ModelName,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        attempt_timeout_s: float | None = None,
        total_timeout_s: float | None = None,
    ) -> BodhanResponse:
        """Send one logical request, retrying transient failures.

        `files` takes httpx-style tuples, e.g. {"file": ("chunk.wav", wav_bytes, "audio/wav")}.
        Bodies are held as bytes, so every retry resends exactly the same payload.
        Raises ToolError; never returns an error response.
        """
        key = self.api_key(model)  # fail before any network use
        url = self._url(path)
        hdrs = {"Authorization": f"Bearer {key}", **(headers or {})}
        per_attempt = attempt_timeout_s or self.config.attempt_timeout_s
        total = total_timeout_s or self.config.total_timeout_s

        start = self._clock()
        deadline = start + total
        attempts = 0
        last_err: ToolError | None = None

        try:
            for attempt in range(self.config.max_retries + 1):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise self._out_of_time(model, total, attempts, last_err)
                attempts += 1
                retry_after: float | None = None
                acquired = False
                try:
                    await asyncio.wait_for(self._sem.acquire(), timeout=remaining)
                    acquired = True
                    self.stats.requests += 1
                    resp = await self._client().request(
                        method,
                        url,
                        headers=hdrs,
                        json=json,
                        data=data,
                        files=files,
                        params=params,
                        timeout=min(per_attempt, remaining),
                    )
                except httpx.TimeoutException:
                    err = ToolError(
                        ErrorCode.UPSTREAM_TIMEOUT,
                        f"The {model} request timed out after {min(per_attempt, remaining):.0f}s.",
                        retryable=True,
                        hint="Retry later, or send a smaller input.",
                    )
                except TimeoutError:
                    err = self._out_of_time(model, total, attempts, last_err)
                except httpx.UnsupportedProtocol:
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Invalid Bodhan base URL: {self.config.base_url!r}.",
                        hint="Set BODHAN_BASE_URL to a full https:// URL, or unset it.",
                    ) from None
                except httpx.TransportError as exc:
                    err = ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Network error talking to Bodhan ({type(exc).__name__}).",
                        retryable=True,
                        hint="Check connectivity; the call is safe to retry.",
                    )
                except httpx.RequestError as exc:
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Request could not be completed ({type(exc).__name__}).",
                    ) from None
                else:
                    if resp.status_code < 400:
                        self.stats.calls[model] += 1
                        return BodhanResponse(
                            status=resp.status_code,
                            headers=dict(resp.headers),
                            content=resp.content,
                            latency_ms=int((self._clock() - start) * 1000),
                            attempts=attempts,
                        )
                    err, retry_after = self._map_http_error(model, resp)
                finally:
                    if acquired:
                        self._sem.release()

                last_err = err
                if not err.retryable or attempt == self.config.max_retries:
                    raise err
                delay = self._backoff(attempt, retry_after)
                if delay >= deadline - self._clock():
                    raise err  # sleeping would burn the whole remaining budget
                self.stats.retries += 1
                await self._sleep(delay)

            raise last_err or ToolError(ErrorCode.INTERNAL, "Retry loop ended unexpectedly.")
        except ToolError:
            self.stats.failures[model] += 1
            raise

    def request_sync(
        self,
        model: ModelName,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        attempt_timeout_s: float | None = None,
        total_timeout_s: float | None = None,
    ) -> BodhanResponse:
        """Synchronous counterpart used by the sync tool and CLI APIs."""
        key = self.api_key(model)
        url = self._url(path)
        hdrs = {"Authorization": f"Bearer {key}", **(headers or {})}
        per_attempt = attempt_timeout_s or self.config.attempt_timeout_s
        total = total_timeout_s or self.config.total_timeout_s
        start = time.monotonic()
        deadline = start + total
        attempts = 0
        last_err: ToolError | None = None

        with self._sync_client_lock:
            if self._sync_http is None:
                self._sync_http = httpx.Client(
                    transport=self._transport,
                    follow_redirects=False,
                )

        try:
            for attempt in range(self.config.max_retries + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._out_of_time(model, total, attempts, last_err)
                attempts += 1
                retry_after: float | None = None
                try:
                    if not self._sync_sem.acquire(timeout=remaining):
                        raise httpx.TimeoutException("Timed out waiting for an HTTP slot.")
                    try:
                        self.stats.requests += 1
                        resp = self._sync_http.request(
                            method,
                            url,
                            headers=hdrs,
                            json=json,
                            data=data,
                            files=files,
                            params=params,
                            timeout=min(per_attempt, remaining),
                        )
                    finally:
                        self._sync_sem.release()
                except httpx.TimeoutException:
                    err = ToolError(
                        ErrorCode.UPSTREAM_TIMEOUT,
                        f"The {model} request timed out after {min(per_attempt, remaining):.0f}s.",
                        retryable=True,
                        hint="Retry later, or send a smaller input.",
                    )
                except httpx.UnsupportedProtocol:
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Invalid Bodhan base URL: {self.config.base_url!r}.",
                        hint="Set BODHAN_BASE_URL to a full https:// URL, or unset it.",
                    ) from None
                except httpx.TransportError as exc:
                    err = ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Network error talking to Bodhan ({type(exc).__name__}).",
                        retryable=True,
                        hint="Check connectivity; the call is safe to retry.",
                    )
                except httpx.RequestError as exc:
                    raise ToolError(
                        ErrorCode.UPSTREAM_ERROR,
                        f"Request could not be completed ({type(exc).__name__}).",
                    ) from None
                else:
                    if resp.status_code < 400:
                        self.stats.calls[model] += 1
                        return BodhanResponse(
                            status=resp.status_code,
                            headers=dict(resp.headers),
                            content=resp.content,
                            latency_ms=int((time.monotonic() - start) * 1000),
                            attempts=attempts,
                        )
                    err, retry_after = self._map_http_error(model, resp)

                last_err = err
                if not err.retryable or attempt == self.config.max_retries:
                    raise err
                delay = self._backoff(attempt, retry_after)
                if delay >= deadline - time.monotonic():
                    raise err
                self.stats.retries += 1
                time.sleep(delay)

            raise last_err or ToolError(ErrorCode.INTERNAL, "Retry loop ended unexpectedly.")
        except ToolError:
            self.stats.failures[model] += 1
            raise

    def _endpoint(self, model: ModelName) -> str:
        return (self._env.get(ENDPOINT_ENV[model]) or DEFAULT_ENDPOINTS[model]).strip()

    @staticmethod
    def _json_payload(response: BodhanResponse) -> dict[str, Any]:
        value = response.json()
        if not isinstance(value, dict):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Bodhan returned JSON in an unexpected format.",
                hint="Check that the configured endpoint returns a JSON object.",
            )
        return value

    @staticmethod
    def _usage(
        model: ModelName,
        payload: Mapping[str, Any],
        latency_ms: int,
        units: Mapping[str, float],
    ) -> Usage:
        raw_usage = payload.get("usage")
        cost = 0.0
        if isinstance(raw_usage, Mapping):
            raw_cost = raw_usage.get("cost_inr", raw_usage.get("cost"))
            if isinstance(raw_cost, int | float) and not isinstance(raw_cost, bool):
                cost = max(0.0, float(raw_cost))
        return Usage(
            model=f"indic-{model}",
            cost_inr=cost,
            latency_ms=latency_ms,
            units=units,
        )


    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str,
        language: str | None = None,
        vad: bool = True,
    ) -> dict[str, Any]:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        data: dict[str, Any] = {"model": "indic-transcribe", "vad": "true" if vad else "false"}
        if language:
            data["language"] = language
        response = self.request_sync(
            "transcribe",
            "POST",
            self._endpoint("transcribe"),
            data=data,
            files={"file": (filename, audio_bytes, mime_type)},
        )
        payload = self._json_payload(response)
        text = payload.get("text", payload.get("transcript"))
        if not isinstance(text, str):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Bodhan transcription response did not contain text.",
                hint="Check the configured transcription endpoint response format.",
            )
        duration_value = payload.get("duration", payload.get("duration_seconds"))
        try:
            duration = float(duration_value) if duration_value is not None else None
        except (TypeError, ValueError):
            duration = None
        return {
            "text": text,
            "language": payload.get("language", language),
            "duration": duration,
            "usage": self._usage(
                "transcribe",
                payload,
                response.latency_ms,
                {"audio_seconds": duration or 0.0},
            ),
        }


    def translate(
        self,
        text: str,
        target_language: str,
        source_language: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": "indic-translate",
            "messages": [{"role": "user", "content": text}],
            "target_language_code": target_language,
            "target_script": "native",
        }
        response = self.request_sync("translate", "POST", self._endpoint("translate"), json=body)
        payload = self._json_payload(response)
        choices = payload.get("choices")
        translated: Any = None
        finish_reason: str | None = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = choices[0].get("finish_reason")
            message = choices[0].get("message")
            if isinstance(message, dict):
                translated = message.get("content")
        if not isinstance(translated, str):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Bodhan chat-completions response did not contain translated text.",
                hint="Check the configured translation endpoint response format.",
            )
        raw_usage = payload.get("usage")
        output_tokens = 0.0
        if isinstance(raw_usage, Mapping):
            raw_tokens = raw_usage.get("completion_tokens", 0)
            if isinstance(raw_tokens, int | float) and not isinstance(raw_tokens, bool):
                output_tokens = float(max(0, raw_tokens))
        return {
            "translated_text": translated,
            "truncated": bool(payload.get("truncated")) or finish_reason == "length",
            "usage": self._usage(
                "translate",
                payload,
                response.latency_ms,
                {
                    "input_characters": float(len(text)),
                    **({"output_tokens": output_tokens} if output_tokens else {}),
                },
            ),
        }

    def speak(
        self,
        text: str,
        language: str,
        voice_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": "indic-speak",
            "input": text,
            "voice": voice_id or DEFAULT_SPEAK_VOICE,
            "instructions": jsonlib.dumps({"lang": language}, ensure_ascii=False),
        }
        response = self.request_sync("speak", "POST", self._endpoint("speak"), json=body)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        extension = (mimetypes.guess_extension(content_type) or ".wav").lstrip(".")
        audio = response.content
        payload: dict[str, Any] = {}
        if content_type == "application/json":
            payload = self._json_payload(response)
            encoded = payload.get("audio_base64")
            if not isinstance(encoded, str):
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    "Bodhan speech response did not contain audio bytes.",
                    hint="Configure an endpoint returning audio or an `audio_base64` JSON field.",
                )
            try:
                audio = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise ToolError(
                    ErrorCode.UPSTREAM_ERROR,
                    "Bodhan returned invalid base64-encoded audio.",
                    hint="Check the configured speech endpoint response format.",
                ) from None
            extension = str(payload.get("format", extension)).lstrip(".").lower()
        if not audio:
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Bodhan returned an empty audio response.",
                hint="Check the configured speech endpoint response format.",
            )
        return {
            "audio_bytes": audio,
            "format": extension,
            "usage": self._usage(
                "speak",
                payload,
                response.latency_ms,
                {"input_characters": float(len(text))},
            ),
        }


    def ocr(
        self,
        image_bytes: bytes,
        filename: str,
        language: str | None = None,
        table_format: str | None = "markdown",
        max_tokens: int | None = 4096,
    ) -> dict[str, Any]:
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if mime_type not in {"image/png", "image/jpeg"}:
            raise ToolError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Indic-OCR requires PNG or JPEG image bytes, got {mime_type!r}.",
                hint="Convert the image to PNG or JPEG before calling OCR.",
            )
        image_url = (
            f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        )
        body: dict[str, Any] = {
            "model": "indic-ocr",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
        }
        if table_format in {"html", "markdown"}:
            body["table_format"] = table_format
        if isinstance(max_tokens, int) and max_tokens > 0:
            body["max_tokens"] = max_tokens
        response = self.request_sync("ocr", "POST", self._endpoint("ocr"), json=body)
        payload = self._json_payload(response)
        choices = payload.get("choices")
        text: Any = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                text = message.get("content")
        if not isinstance(text, str):
            raise ToolError(
                ErrorCode.UPSTREAM_ERROR,
                "Bodhan chat-completions response did not contain OCR text.",
                hint="Check the configured OCR endpoint response format.",
            )
        return {
            "text": text,
            "usage": self._usage("ocr", payload, response.latency_ms, {"images": 1.0}),
        }

    # -- helpers

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        """Equal-jitter exponential backoff: half fixed, half random, so synchronized
        callers spread out without ever retrying instantly."""
        cfg = self.config
        ceiling = min(cfg.backoff_cap_s, cfg.backoff_base_s * (2**attempt))
        delay = ceiling / 2 + self._rng.uniform(0, ceiling / 2)
        if retry_after is not None:
            delay = max(delay, min(retry_after, cfg.retry_after_cap_s))
        return delay

    def _out_of_time(
        self, model: str, total: float, attempts: int, last_err: ToolError | None
    ) -> ToolError:
        if last_err is not None:
            return last_err
        return ToolError(
            ErrorCode.UPSTREAM_TIMEOUT,
            f"The {model} call exceeded its {total:.0f}s limit after {attempts} attempt(s).",
            retryable=True,
            hint="Retry later or send a smaller input.",
        )

    def _map_http_error(self, model: str, resp: httpx.Response) -> tuple[ToolError, float | None]:
        status = resp.status_code
        code, message = _extract_error(resp)
        retry_after = _parse_retry_after(resp.headers.get("retry-after"))
        detail = {"status": status, "upstream_message": message} if message else {"status": status}
        key_env = KEY_ENV.get(model, "the API key")

        quota_text = f"{code or ''} {message or ''}".lower()
        out_of_credit = status == 402 or (
            status == 429 and any(m in quota_text for m in _QUOTA_MARKERS)
        )
        if out_of_credit:
            return ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Bodhan reports the account is out of credit or quota for {model}.",
                retryable=False,
                hint="Top up credit in the Bodhan console; retrying will not help.",
                details=detail,
            ), None
        if status == 429:
            return ToolError(
                ErrorCode.UPSTREAM_RATE_LIMITED,
                f"Bodhan rate-limited the {model} request.",
                retryable=True,
                hint="Wait and retry, or lower BODHAN_MAX_CONCURRENCY.",
                details=detail,
            ), retry_after
        if status in (401, 403):
            return ToolError(
                ErrorCode.AUTH_MISSING,
                f"Bodhan rejected the {model} API key (HTTP {status}).",
                retryable=False,
                hint=f"Check that {key_env} holds a valid key issued for the {model} model.",
                details=detail,
            ), None
        if status == 415:
            return ToolError(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Bodhan does not accept this input format for {model}.",
                hint=(
                    "Convert the input to a supported format (e.g. WAV for audio, PNG for images)."
                ),
                details=detail,
            ), None
        if status in (400, 413, 422):
            return ToolError(
                ErrorCode.INVALID_ARGUMENT,
                f"Bodhan rejected the {model} request (HTTP {status})"
                + (f": {message}" if message else "."),
                hint="The input or parameters are invalid for this model. If the input was "
                "within documented limits, this is a bug in the tool's chunking.",
                details=detail,
            ), None
        if status == 504 or status == 408:
            return ToolError(
                ErrorCode.UPSTREAM_TIMEOUT,
                f"Bodhan timed out handling the {model} request (HTTP {status}).",
                retryable=True,
                hint="Retry later, or send a smaller input.",
                details=detail,
            ), retry_after
        if status in _RETRYABLE_STATUS:
            return ToolError(
                ErrorCode.UPSTREAM_ERROR,
                f"Bodhan had a server error on {model} (HTTP {status}).",
                retryable=True,
                hint="Transient; retry later.",
                details=detail,
            ), retry_after
        return ToolError(
            ErrorCode.UPSTREAM_ERROR,
            f"Unexpected response from Bodhan for {model} (HTTP {status})"
            + (f": {message}" if message else "."),
            retryable=False,
            hint="If this is a 404, check BODHAN_BASE_URL and the tool's endpoint path.",
            details=detail,
        ), None


# ------------------------------------------------------------------ response parsing


def _extract_error(resp: httpx.Response) -> tuple[str | None, str | None]:
    """Best-effort (code, message) from an error body. Handles OpenAI-style
    {"error": {"message", "code", "type"}}, {"message": ...} and {"detail": ...}.
    The message is truncated; headers are never included."""
    try:
        body = resp.json()
    except ValueError:
        text = resp.text.strip()
        return None, (text[:_MAX_ERROR_TEXT] or None)

    code: str | None = None
    message: str | None = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = err.get("message") if isinstance(err.get("message"), str) else None
            raw_code = err.get("code") or err.get("type")
            code = str(raw_code) if raw_code is not None else None
        elif isinstance(err, str):
            message = err
        if message is None:
            for field_name in ("message", "detail"):
                v = body.get(field_name)
                if isinstance(v, str):
                    message = v
                    break
                if v is not None:
                    message = jsonlib.dumps(v, ensure_ascii=False)
                    break
    elif isinstance(body, str):
        message = body
    return code, (message[:_MAX_ERROR_TEXT] if message else None)


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After in seconds. HTTP-date form is ignored (rare for APIs; falls back to backoff)."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
