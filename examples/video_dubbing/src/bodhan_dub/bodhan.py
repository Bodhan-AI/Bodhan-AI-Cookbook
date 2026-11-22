"""Thin client for the three Bodhan models this pipeline uses.

Every call goes through `_post`, which gives us in one place:
  * a concurrency cap (a semaphore shared by all threads),
  * retries with exponential backoff + jitter on 429 / 5xx / network errors only,
  * a usage meter that prices each call so report.json can show cost.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import wave
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import requests
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from .cache import DiskCache
from .config import Settings
from .languages import LANGUAGE_NAMES

log = logging.getLogger(__name__)

TRANSCRIBE_MODEL = "indic-transcribe"
SPEAK_MODEL = "indic-speak"
TRANSLATE_MODEL = "indic-translate"

# Hosted prices in INR, from the assignment brief.
PRICE_TRANSCRIBE_PER_SECOND = 0.10 / 60
PRICE_TRANSLATE_PER_OUTPUT_TOKEN = 0.20 / 10_000
PRICE_SPEAK_PER_CHAR = 6.0 / 10_000

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class BodhanError(RuntimeError):
    """A failed Bodhan call. `status` 0 means the request never got an HTTP response."""

    def __init__(self, status: int, code: str, message: str, request_id: str | None = None):
        super().__init__(f"{status} {code}: {message}" + (f" (request_id={request_id})" if request_id else ""))
        self.status = status
        self.code = code
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.code != BUDGET_EXCEEDED and (self.status == 0 or self.status in RETRYABLE_STATUS)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, BodhanError) and exc.retryable


BUDGET_EXCEEDED = "budget_exceeded"


def _error_from(resp: requests.Response) -> BodhanError:
    try:
        err = resp.json()["error"]
        code, message, request_id = err.get("code", "unknown"), err.get("message", ""), err.get("request_id")
    except (ValueError, KeyError, TypeError):
        code, message, request_id = "http_error", resp.text[:300], resp.headers.get("x-request-id")
    # A 429 means either "slow down" or "this key's credit is spent" (seen live: "ExceededBudget ...
    # over budget"). Only the first is worth waiting out; retrying the second just burns time.
    if resp.status_code in (402, 429) and "budget" in f"{code} {message}".lower():
        # The server's text names the account; keep it out of reports and logs.
        code = BUDGET_EXCEEDED
        message = "Bodhan credit exhausted for this key; add credit and rerun (cached results are reused)"
    return BodhanError(resp.status_code, code, message, request_id)


def chat_translate_prompt(text: str, target_language: str, script: str = "native") -> str:
    """The single user turn for the chat transport, mirroring /translate's target + script."""
    name = LANGUAGE_NAMES.get(target_language, target_language)
    qualifier = {"native": "", "roman": " in Roman script", "codemix": " (code-mixed)"}[script]
    return f"Translate to {name}{qualifier}: {text}"


def wav_duration(data: bytes) -> float:
    """Duration in seconds of a PCM WAV held in memory."""
    with wave.open(io.BytesIO(data), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


@dataclass
class UsageMeter:
    """Thread-safe running totals of calls, retries, billable units and cost per model."""

    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    retries: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    units: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    cost_inr: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    cache_hits: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cost_by_lang: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    cooldown_s: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    latencies_ms: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, units: float, cost: float, lang: str = "shared") -> None:
        """`lang` is the dub language the call served ("shared" for source transcription)."""
        with self._lock:
            self.calls[model] += 1
            self.units[model] += units
            self.cost_inr[model] += cost
            self.cost_by_lang[lang] += cost

    def record_retry(self, model: str) -> None:
        with self._lock:
            self.retries[model] += 1

    def record_hit(self, model: str) -> None:
        with self._lock:
            self.cache_hits[model] += 1

    def record_cooldown(self, model: str, seconds: float) -> None:
        with self._lock:
            self.cooldown_s[model] += seconds

    def record_latency(self, model: str, ms: int) -> None:
        with self._lock:
            self.latencies_ms[model].append(ms)

    def latency_summary(self) -> dict[str, dict[str, int]]:
        out = {}
        for model, values in self.latencies_ms.items():
            ordered = sorted(values)
            out[model] = {"calls": len(ordered), "median_ms": ordered[len(ordered) // 2],
                          "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]}
        return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": dict(self.calls),
                "cache_hits": dict(self.cache_hits),
                "retries": dict(self.retries),
                "units": {k: round(v, 3) for k, v in self.units.items()},
                "cost_inr": {k: round(v, 4) for k, v in self.cost_inr.items()},
                "total_cost_inr": round(sum(self.cost_inr.values()), 4),
                "cost_inr_by_language": {k: round(v, 4) for k, v in self.cost_by_lang.items()},
                "rate_limit_wait_s": {k: round(v, 1) for k, v in self.cooldown_s.items()},
                "latency": self.latency_summary(),
            }


@dataclass(frozen=True)
class Translation:
    text: str
    truncated: bool
    output_tokens: int


class BodhanClient:
    def __init__(
        self,
        settings: Settings,
        *,
        max_parallel: int = 4,
        timeout_s: float = 120.0,
        max_attempts: int = 8,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 30.0,
        session: requests.Session | None = None,
        cache: DiskCache | None = None,
    ):
        self.settings = settings
        self.cache = cache
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.backoff_initial_s = backoff_initial_s
        self.backoff_max_s = backoff_max_s
        self.session = session or requests.Session()
        self.usage = UsageMeter()
        self._slots = threading.BoundedSemaphore(max_parallel)
        self._translate_via_chat = False
        self._route_known = False
        self._route_lock = threading.Lock()
        # Shared rate-limit state per model. The API sends no rate-limit headers, so we
        # learn the limit from 429s: one thread's 429 pauses every thread for that model,
        # instead of all of them hammering the limit and burning their retries.
        self._cooldown_until: dict[str, float] = defaultdict(float)
        self._consecutive_429: dict[str, int] = defaultdict(int)
        self._rate_lock = threading.Lock()

    def _wait_for_cooldown(self, model: str) -> None:
        while (delay := self._cooldown_until[model] - time.monotonic()) > 0:
            time.sleep(delay)

    def _on_rate_limited(self, model: str, retry_after: str | None) -> float:
        with self._rate_lock:
            self._consecutive_429[model] += 1
            try:
                pause = float(retry_after) if retry_after else 0.0
            except ValueError:
                pause = 0.0
            if pause <= 0:
                pause = min(self.backoff_initial_s * 2 ** (self._consecutive_429[model] - 1), self.backoff_max_s)
            self._cooldown_until[model] = max(self._cooldown_until[model], time.monotonic() + pause)
        self.usage.record_cooldown(model, pause)
        return pause

    def _on_success(self, model: str) -> None:
        if self._consecutive_429[model]:
            with self._rate_lock:
                self._consecutive_429[model] = 0

    def _post(self, model: str, path: str, key: str, **kwargs: Any) -> requests.Response:
        if not key:
            raise BodhanError(401, "missing_api_key", f"no API key configured for {model}")
        url = f"{self.settings.base_url}{path}"
        headers = {"Authorization": f"Bearer {key}", **kwargs.pop("headers", {})}

        def attempt() -> requests.Response:
            self._wait_for_cooldown(model)
            with self._slots:
                started = time.perf_counter()
                try:
                    resp = self.session.post(url, headers=headers, timeout=self.timeout_s, **kwargs)
                except requests.RequestException as exc:
                    raise BodhanError(0, "connection_error", str(exc)) from exc
                latency_ms = round((time.perf_counter() - started) * 1000)
            if not resp.ok:
                err = _error_from(resp)
                extra = {"model": model, "status": err.status, "code": err.code, "latency_ms": latency_ms,
                         "error": str(err)[:300]}
                if err.status == 429 and err.retryable:
                    extra["cooldown_s"] = round(self._on_rate_limited(model, resp.headers.get("retry-after")), 2)
                log.warning("bodhan call failed", extra=extra)
                raise err
            self._on_success(model)
            self.usage.record_latency(model, latency_ms)
            log.debug("bodhan call ok", extra={"model": model, "latency_ms": latency_ms,
                                               "request_id": resp.headers.get("x-request-id")})
            return resp

        def before_sleep(state: Any) -> None:
            self.usage.record_retry(model)

        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(multiplier=self.backoff_initial_s, max=self.backoff_max_s),
            retry=retry_if_exception(_is_retryable),
            before_sleep=before_sleep,
            reraise=True,
        )
        return retrying(attempt)

    def transcribe(self, wav: bytes, language: str = "en") -> str:
        """Speech to text for one clip of at most 30 s. Returns the plain transcript."""
        key = DiskCache.key(TRANSCRIBE_MODEL, {"language": language}, wav)
        if (hit := self._cached_json(TRANSCRIBE_MODEL, key)) is not None:
            return hit["text"]
        seconds = wav_duration(wav)
        resp = self._post(
            TRANSCRIBE_MODEL, "/v1/audio/transcriptions", self.settings.transcribe_key,
            files={"file": ("clip.wav", wav, "audio/wav")},
            data={"model": TRANSCRIBE_MODEL, "language": language},
        )
        self.usage.record(TRANSCRIBE_MODEL, seconds, seconds * PRICE_TRANSCRIBE_PER_SECOND,
                          "shared" if language == "en" else language)
        text = str(resp.json().get("text", "")).strip()
        if self.cache:
            self.cache.put_json(key, {"text": text})
        return text

    def _cached_json(self, model: str, key: str) -> Any | None:
        if not self.cache:
            return None
        hit = self.cache.get_json(key)
        if hit is not None:
            self.usage.record_hit(model)
        return hit

    def translate(self, text: str, target_language: str, script: str = "native",
                  bill_to: str | None = None) -> Translation:
        """Translate `text` into `target_language`. Check `truncated` before trusting the result.

        The documented product route is POST /translate, but some keys only reach the
        equivalent chat-completions transport (/translate answers 404). We try /translate
        once, and on 404 switch to the chat transport for the rest of the run.
        """
        key = DiskCache.key(TRANSLATE_MODEL, {"target_language": target_language, "script": script}, text)
        if (hit := self._cached_json(TRANSLATE_MODEL, key)) is not None:
            return Translation(**hit)
        result = self._translate_live(text, target_language, script)
        # Back-translations target English; `bill_to` names the dub language they check.
        self.usage.record(TRANSLATE_MODEL, result.output_tokens,
                          result.output_tokens * PRICE_TRANSLATE_PER_OUTPUT_TOKEN, bill_to or target_language)
        if self.cache:
            self.cache.put_json(key, {"text": result.text, "truncated": result.truncated,
                                      "output_tokens": result.output_tokens})
        return result

    def _translate_live(self, text: str, target_language: str, script: str) -> Translation:
        if not self._route_known:
            # The first call settles which route works; other threads wait instead of all probing.
            with self._route_lock:
                if not self._route_known:
                    result = self._translate_route_probe(text, target_language, script)
                    self._route_known = True
                    return result
        if self._translate_via_chat:
            return self._translate_chat(text, target_language, script)
        return self._translate_route_probe(text, target_language, script)

    def _translate_route_probe(self, text: str, target_language: str, script: str) -> Translation:
        if self._translate_via_chat:
            return self._translate_chat(text, target_language, script)
        try:
            resp = self._post(
                TRANSLATE_MODEL, "/translate", self.settings.translate_key,
                # Documented minimum: `script` only when it isn't the default (it never applies to English).
                json={"text": text, "target_language": target_language,
                      **({"script": script} if script != "native" and target_language != "en" else {})},
            )
        except BodhanError as exc:
            if exc.status != 404:
                raise
            log.info("/translate not available for this key; using the chat transport")
            self._translate_via_chat = True
            return self._translate_chat(text, target_language, script)
        body = resp.json()
        if "error" in body:  # the docs say to branch on presence, not only on status
            raise _error_from(resp)
        tokens = int(body.get("usage", {}).get("completion_tokens", 0))
        return Translation(text=body["translation"], truncated=bool(body.get("truncated")), output_tokens=tokens)

    def _translate_chat(self, text: str, target_language: str, script: str) -> Translation:
        # One user turn, no system turn (the endpoint rejects anything else). The target
        # must always be named: given bare text the model picks a language on its own.
        resp = self._post(
            TRANSLATE_MODEL, "/v1/chat/completions", self.settings.translate_key,
            json={"model": TRANSLATE_MODEL,
                  "messages": [{"role": "user", "content": chat_translate_prompt(text, target_language, script)}]},
        )
        body = resp.json()
        choice = body["choices"][0]
        tokens = int(body.get("usage", {}).get("completion_tokens", 0))
        return Translation(text=(choice["message"].get("content") or "").strip(),
                           truncated=choice.get("finish_reason") == "length", output_tokens=tokens)

    def speak(self, text: str, voice: str, lang: str, style: str | None = None, variant: int = 0) -> bytes:
        """Text to speech. Returns WAV bytes (PCM16, 24 kHz, mono).

        `variant` > 0 asks for a fresh take of the same text (a new cache entry), used when
        QA rejects a clip because Indic-Speak repeated or dropped words.
        """
        instructions: dict[str, str] = {"lang": lang}
        if style:
            instructions["style"] = style
        key = DiskCache.key(SPEAK_MODEL, {"voice": voice, **instructions, **({"variant": variant} if variant else {})},
                            text)
        if self.cache and (audio := self.cache.get_bytes(key)) is not None:
            self.usage.record_hit(SPEAK_MODEL)
            return audio
        resp = self._post(
            SPEAK_MODEL, "/v1/audio/speech", self.settings.speak_key,
            headers={"Content-Type": "application/json"},
            json={"model": SPEAK_MODEL, "input": text, "voice": voice, "instructions": json.dumps(instructions)},
        )
        if not resp.content.startswith(b"RIFF"):
            raise BodhanError(resp.status_code, "bad_audio", f"expected WAV, got {resp.headers.get('content-type')}")
        self.usage.record(SPEAK_MODEL, len(text), len(text) * PRICE_SPEAK_PER_CHAR, lang)
        if self.cache:
            self.cache.put_bytes(key, resp.content)
        return resp.content
