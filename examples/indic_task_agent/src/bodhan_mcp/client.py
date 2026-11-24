"""Everything about talking to the Bodhan API: settings and keys, structured errors, credential
redaction, the response cache and the async HTTP client.

Keys: one per Bodhan model (the console issues model-scoped keys). Paths: BODHAN_MCP_WORKDIR for
outputs, BODHAN_CACHE_DIR for the response cache.

Every failure a caller can see is a ToolFailure with a stable `code`, a plain-English `message` and a
`hint` that says what to do next; the server turns it into {"error": {"code", "message", "hint"}}.

The cache is content-addressed (model, endpoint, parameters and the exact input bytes), so the same input
is never sent to the same model twice, within a run or across reruns.

Behaviour measured with the probes (see probes/FINDINGS.md):
- translate / transliterate / OCR: POST /v1/chat/completions; transcribe and speak: /v1/audio/*.
- No rate-limit headers were seen; a 500 can be caused by a bad voice/style (validated before calling).
- indic-speak answers bursts of parallel requests with 429 "Maximum rate limit reached": after a 429,
  every request to that model waits out a shared cooldown, and rate limits get longer backoff.
- A 200 with an empty or null body is an error.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, wait_exponential_jitter

from .text import garbled_reason

BASE_URL = "https://api.bodhan.ai"

TRANSCRIBE = "indic-transcribe"
TRANSLATE = "indic-translate"
TRANSLITERATE = "indic-transliterate"
SPEAK = "indic-speak"
OCR = "indic-ocr"

MODEL_KEY_ENV = {
    TRANSCRIBE: "BODHAN_TRANSCRIBE_KEY",
    TRANSLATE: "BODHAN_TRANSLATE_KEY",
    SPEAK: "BODHAN_SPEAK_KEY",
    OCR: "BODHAN_OCR_KEY",
    TRANSLITERATE: "BODHAN_TRANSLITERATE_KEY",
}


def clean_env_value(value: str | None) -> str:
    """Treat empty values and `<placeholder>` values copied from .env.example as unset."""
    value = (value or "").strip()
    return "" if not value or value.startswith("<") else value


@dataclass(frozen=True)
class Settings:
    keys: Mapping[str, str]
    workdir: Path
    cache_dir: Path
    base_url: str = BASE_URL
    concurrency: int = 4
    request_timeout: float = 120.0
    max_attempts: int = 4
    retry_initial: float = 1.0
    retry_max: float = 15.0
    rate_limit_attempts: int = 6  # a 429 rate limit clears with time, so it gets more patience
    rate_limit_max_wait: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        keys = {model: clean_env_value(env.get(var)) for model, var in MODEL_KEY_ENV.items()}
        workdir = Path(clean_env_value(env.get("BODHAN_MCP_WORKDIR")) or "bodhan_mcp_out").expanduser().resolve()
        cache_dir = Path(clean_env_value(env.get("BODHAN_CACHE_DIR")) or ".cache/bodhan_mcp").expanduser().resolve()
        concurrency = int(clean_env_value(env.get("BODHAN_CONCURRENCY")) or 4)
        return cls(keys=keys, workdir=workdir, cache_dir=cache_dir, concurrency=max(1, concurrency))

    def key_for(self, model: str) -> str:
        key = self.keys.get(model, "")
        if not key:
            var = MODEL_KEY_ENV[model]
            raise ToolFailure(
                Code.MISSING_API_KEY,
                f"No API key configured for {model}.",
                f"Set {var} in the server's environment (a key for {model} from https://console.bodhan.ai).",
            )
        return key


class Code:
    """Stable error codes a caller can branch on."""

    # Input problems the caller can fix.
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NOT_A_FILE = "NOT_A_FILE"
    EMPTY_FILE = "EMPTY_FILE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    UNSUPPORTED_VOICE = "UNSUPPORTED_VOICE"
    UNSUPPORTED_STYLE = "UNSUPPORTED_STYLE"
    TEXT_LANGUAGE_MISMATCH = "TEXT_LANGUAGE_MISMATCH"
    EMPTY_TEXT = "EMPTY_TEXT"
    EMPTY_AUDIO = "EMPTY_AUDIO"
    NO_AUDIO_TRACK = "NO_AUDIO_TRACK"
    INVALID_IMAGE = "INVALID_IMAGE"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"

    # Environment / configuration.
    MISSING_API_KEY = "MISSING_API_KEY"
    FFMPEG_NOT_FOUND = "FFMPEG_NOT_FOUND"
    MEDIA_ERROR = "MEDIA_ERROR"

    # Upstream (Bodhan API) problems.
    AUTH_FAILED = "AUTH_FAILED"
    UPSTREAM_REJECTED = "UPSTREAM_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    OUT_OF_CREDIT = "OUT_OF_CREDIT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    GARBLED_OUTPUT = "GARBLED_OUTPUT"
    UPSTREAM_UNREACHABLE = "UPSTREAM_UNREACHABLE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    TIMEOUT = "TIMEOUT"

    INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolFailure(Exception):
    """A failure with a stable code, a readable message and an actionable hint."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "hint": self.hint}

    def to_json(self) -> str:
        return json.dumps({"error": self.to_dict()}, ensure_ascii=False)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f" Hint: {self.hint}" if self.hint else "")


_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-*.]{4,}"), "sk-***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=]+"), "Bearer ***"),
    (re.compile(r"(?i)(key hash \(token\)\s*=\s*)[0-9a-f]{16,}"), r"\1***"),
    (re.compile(r"(?i)(received api key\s*=\s*)\S+"), r"\1***"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


@dataclass
class CacheEntry:
    data: dict
    blob: bytes | None


class Cache:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            " key TEXT PRIMARY KEY, model TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL, blob BLOB)"
        )
        self._db.commit()

    @staticmethod
    def make_key(model: str, endpoint: str, params: dict, payload: bytes) -> str:
        head = json.dumps({"model": model, "endpoint": endpoint, "params": params}, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256()
        h.update(head.encode("utf-8"))
        h.update(b"\x00")
        h.update(hashlib.sha256(payload).digest())
        return h.hexdigest()

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            row = self._db.execute("SELECT data, blob FROM responses WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return CacheEntry(json.loads(row[0]), row[1])

    def put(self, key: str, model: str, data: dict, blob: bytes | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (key, model, created, data, blob) VALUES (?, ?, ?, ?, ?)",
                (key, model, time.time(), json.dumps(data, ensure_ascii=False), blob),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()


log = logging.getLogger("bodhan_mcp.client")

_CREDIT_WORDS = ("credit", "balance", "budget", "quota", "insufficient", "exceeded your")


class UpstreamHTTPError(Exception):
    def __init__(self, status: int, message: str, retry_after: float | None = None) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message
        self.retry_after = retry_after

    @property
    def rate_limited(self) -> bool:
        return self.status == 429 and not any(w in self.message.lower() for w in _CREDIT_WORDS)


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers.get("retry-after", "")))
    except ValueError:
        return None


@dataclass
class ChatResult:
    content: str
    finish_reason: str | None
    completion_tokens: int
    prompt_tokens: int
    extra: dict  # other top-level response fields (e.g. OCR `blocks`)
    cached: bool


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return redact(resp.text[:500])
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return redact(str(err["message"]))[:800]
        if body.get("detail"):
            return redact(str(body["detail"]))[:800]
    return redact(json.dumps(body)[:500])


class _RetryPolicy:
    """Retry 429/502/503/504, timeouts and connection errors; a 500 at most once; never other 4xx."""

    def __init__(self) -> None:
        self.server_errors = 0

    def __call__(self, exc: BaseException) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(exc, UpstreamHTTPError):
            if exc.status == 429:
                return exc.rate_limited
            if exc.status in (502, 503, 504):
                return True
            if exc.status == 500:
                self.server_errors += 1
                return self.server_errors <= 1
        return False


class BodhanClient:
    def __init__(self, settings: Settings, cache: Cache, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.cache = cache
        self._http = http or httpx.AsyncClient(timeout=settings.request_timeout)
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._cooldown: dict[str, float] = {}  # model -> monotonic time before which nothing is sent

    async def aclose(self) -> None:
        await self._http.aclose()

    def _sem(self, model: str) -> asyncio.Semaphore:
        if model not in self._semaphores:
            self._semaphores[model] = asyncio.Semaphore(self.settings.concurrency)
        return self._semaphores[model]

    # --- transport -----------------------------------------------------------------------

    def _stop(self, state: RetryCallState) -> bool:
        exc = state.outcome.exception() if state.outcome else None
        rate_limited = isinstance(exc, UpstreamHTTPError) and exc.rate_limited
        limit = self.settings.rate_limit_attempts if rate_limited else self.settings.max_attempts
        return state.attempt_number >= limit

    def _wait(self, model: str):
        default = wait_exponential_jitter(multiplier=self.settings.retry_initial, max=self.settings.retry_max)

        def wait(state: RetryCallState) -> float:
            exc = state.outcome.exception() if state.outcome else None
            if not (isinstance(exc, UpstreamHTTPError) and exc.rate_limited):
                return default(state)
            backoff = min(self.settings.rate_limit_max_wait, 2 * self.settings.retry_initial * 2 ** state.attempt_number)
            delay = max(exc.retry_after or 0.0, backoff) * random.uniform(0.8, 1.2)
            # Shared cooldown: the model's other in-flight requests wait too instead of piling on.
            self._cooldown[model] = max(self._cooldown.get(model, 0.0), time.monotonic() + delay)
            return delay
        return wait

    async def _post(self, model: str, path: str, **kwargs) -> httpx.Response:
        key = self.settings.key_for(model)
        url = self.settings.base_url.rstrip("/") + path
        policy = _RetryPolicy()
        try:
            async for attempt in AsyncRetrying(stop=self._stop, wait=self._wait(model),
                                               retry=retry_if_exception(policy), reraise=True):
                with attempt:
                    async with self._sem(model):
                        pause = self._cooldown.get(model, 0.0) - time.monotonic()
                        if pause > 0:
                            await asyncio.sleep(pause)
                        resp = await self._http.post(url, headers={"Authorization": f"Bearer {key}"}, **kwargs)
                    if resp.status_code >= 400:
                        log.warning("%s %s -> %s (attempt %d)", model, path, resp.status_code,
                                    attempt.retry_state.attempt_number)
                        raise UpstreamHTTPError(resp.status_code, _error_message(resp), _retry_after(resp))
        except UpstreamHTTPError as exc:
            raise self._map_http_error(model, exc) from None
        except httpx.TimeoutException:
            raise ToolFailure(Code.TIMEOUT, f"{model} did not respond within {self.settings.request_timeout:.0f}s "
                              f"after {self.settings.max_attempts} attempts.",
                              "Retry later, or send a smaller input.") from None
        except httpx.TransportError as exc:
            raise ToolFailure(Code.UPSTREAM_UNREACHABLE, f"Could not reach the Bodhan API: {redact(str(exc))}",
                              "Check network access to https://api.bodhan.ai and retry.") from None
        return resp

    def _map_http_error(self, model: str, exc: UpstreamHTTPError) -> ToolFailure:
        msg = f"{model} returned HTTP {exc.status}: {exc.message}"
        if exc.status in (401, 403):
            return ToolFailure(Code.AUTH_FAILED, msg, f"Check the API key for {model} (one key per model).")
        if exc.status == 429:
            if any(w in exc.message.lower() for w in _CREDIT_WORDS):
                return ToolFailure(Code.OUT_OF_CREDIT, msg, "Top up Bodhan credit; retrying will not help.")
            return ToolFailure(Code.RATE_LIMITED, msg, "Rate limited after retries; wait and retry.")
        if 400 <= exc.status < 500:
            return ToolFailure(Code.UPSTREAM_REJECTED, msg, "Fix the request as the message describes.")
        return ToolFailure(Code.UPSTREAM_ERROR, msg, "Bodhan had a server error; retry later.")

    async def _cached(self, model: str, key: str, producer, accept=None):
        """Return (data, blob, cached). Concurrent identical requests share one upstream call.
        `accept(data)` False means the response is unusable: it is neither cached nor served from the cache."""
        hit = self.cache.get(key)
        if hit is not None and (accept is None or accept(hit.data)):
            return hit.data, hit.blob, True
        if key in self._inflight:
            data, blob = await asyncio.shield(self._inflight[key])
            return data, blob, True
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            data, blob = await producer()
            if accept is None or accept(data):
                self.cache.put(key, model, data, blob)
            future.set_result((data, blob))
            return data, blob, False
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved: no "exception never retrieved" warning without waiters
            raise
        finally:
            self._inflight.pop(key, None)

    # --- endpoints -----------------------------------------------------------------------

    async def chat(self, model: str, content, params: dict, cache_payload: bytes) -> ChatResult:
        """POST /v1/chat/completions with one user message. `params` are extra top-level fields."""
        key = Cache.make_key(model, "chat", params, cache_payload)

        async def produce():
            body = {"model": model, "messages": [{"role": "user", "content": content}], **params}
            resp = await self._post(model, "/v1/chat/completions", json=body)
            try:
                data = resp.json() if resp.content else None
            except ValueError:
                data = None
            if not isinstance(data, dict) or not data.get("choices"):
                raise ToolFailure(Code.EMPTY_RESPONSE, f"{model} returned an empty response.",
                                  "The input may be unreadable (e.g. not a real image); check it and retry.")
            return data, None

        data, _, cached = await self._cached(model, key, produce)
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        extra = {k: v for k, v in data.items() if k not in ("choices", "usage", "id", "object", "created", "model")}
        return ChatResult(
            content=(choice.get("message") or {}).get("content") or "",
            finish_reason=choice.get("finish_reason"),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            extra=extra,
            cached=cached,
        )

    async def transcribe(self, wav: bytes, language: str | None) -> tuple[str, bool, int]:
        """Return (text, cached, billed_calls). Garbled output (one character looping, no words) is retried
        once with a fresh call and never cached; if it is garbled again, GARBLED_OUTPUT is raised."""
        params = {"language": language} if language else {}
        key = Cache.make_key(TRANSCRIBE, "transcriptions", params, wav)

        async def produce():
            resp = await self._post(TRANSCRIBE, "/v1/audio/transcriptions",
                                    data={"model": TRANSCRIBE, **params},
                                    files={"file": ("audio.wav", wav, "audio/wav")})
            try:
                data = resp.json()
            except ValueError:
                data = None
            if not isinstance(data, dict) or "text" not in data:
                raise ToolFailure(Code.EMPTY_RESPONSE, "indic-transcribe returned no text field.", "Retry later.")
            return {"text": data.get("text") or ""}, None

        def usable(data: dict) -> bool:
            return garbled_reason(data.get("text") or "") is None

        calls = 0
        for _ in range(2):
            data, _, cached = await self._cached(TRANSCRIBE, key, produce, accept=usable)
            calls += 0 if cached else 1
            if usable(data):
                return data["text"], cached, calls
        reason = garbled_reason(data.get("text") or "")
        failure = ToolFailure(Code.GARBLED_OUTPUT, f"indic-transcribe returned garbled text twice ({reason}).",
                              "Retry with a language hint if you can guess the language; otherwise report this "
                              "audio as unreadable. Do not guess its language from the garbled text.")
        failure.billed_calls = calls
        raise failure

    async def speak(self, text: str, voice: str, lang: str, style: str | None) -> tuple[bytes, bool]:
        instructions = {"lang": lang, **({"style": style} if style else {})}
        params = {"voice": voice, "instructions": instructions}
        key = Cache.make_key(SPEAK, "speech", params, text.encode("utf-8"))

        async def produce():
            body = {"model": SPEAK, "input": text, "voice": voice, "instructions": json.dumps(instructions)}
            resp = await self._post(SPEAK, "/v1/audio/speech", json=body)
            if not resp.content.startswith(b"RIFF"):
                raise ToolFailure(Code.EMPTY_RESPONSE, "indic-speak did not return WAV audio.", "Retry later.")
            return {"bytes": len(resp.content)}, resp.content

        _, blob, cached = await self._cached(SPEAK, key, produce)
        return blob, cached

    async def ocr(self, image: bytes, mime: str, table_format: str) -> ChatResult:
        url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        content = [{"type": "image_url", "image_url": {"url": url}}]
        return await self.chat(OCR, content, {"table_format": table_format}, image)
