"""Clients for Bodhan models and the judge LLM.

Every call goes through the same path:

    cache lookup -> budget reservation -> concurrency slot -> HTTP with retries -> cache write

Bodhan endpoints (https://console.bodhan.ai/api-docs/):
  POST /translate                 indic-translate      (no model field; target_language only)
  POST /transliterate             indic-transliterate
  POST /v1/chat/completions       either text model    (documented second transport; used when a route answers 404)
  POST /v1/audio/speech           indic-speak          (WAV PCM16 24 kHz mono)
  POST /v1/audio/transcriptions   indic-transcribe     (<= 30 s of audio per request)

The LLM is any OpenAI-compatible chat endpoint (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY).
It is only ever asked to *read* and *classify*; it never produces Indian-language text.
"""

from __future__ import annotations

import io
import json
import logging
import random
import re
import threading
import time
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeVar

import openai
import requests

from .budget import Budget, BudgetExceeded
from .cache import Cache, bytes_hash
from .config import Settings, defaults

log = logging.getLogger("bodhan_eval.clients")
T = TypeVar("T")

TRANSLATE_MODEL = "indic-translate"
TRANSLITERATE_MODEL = "indic-transliterate"
SPEAK_MODEL = "indic-speak"
TRANSCRIBE_MODEL = "indic-transcribe"
CHAT_PATH = "/v1/chat/completions"  # Bodhan's second, documented transport for the text models

# One Bodhan key per model (keys are scoped per model); see Settings.from_env for the fallbacks.
KEY_ENV = {
    "translate": "BODHAN_TRANSLATE_KEY",
    "transliterate": "BODHAN_TRANSLITERATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
}
# Transliteration is priced per output token like translation and reported on the translate cost line,
# so the cost breakdown in summary.json keeps its keys.
COST_LINE = {"transliterate": "translate"}


class ServiceUnavailable(RuntimeError):
    """A model cannot be called (no key configured). Checks that need it skip themselves."""


class ApiError(RuntimeError):
    """An HTTP error that retrying will not fix, or retries that ran out (``status`` 0, ``code`` retries_exhausted)."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code


class BodhanError(ApiError):
    """From a Bodhan model."""


class LLMError(ApiError):
    """From the judge LLM endpoint."""


class _Retryable(Exception):
    """A failed attempt worth repeating: 429, 408/409, 5xx, timeout or connection error."""

    def __init__(self, msg: str, retry_after: float | None = None, rate_limited: bool = False):
        super().__init__(msg)
        self.retry_after = retry_after
        self.rate_limited = rate_limited  # HTTP 429: the service asks us to slow down -> halve its concurrency


_sleep = time.sleep  # the only place a back-off waits (tests record the waits instead of sleeping)
_now = time.monotonic  # the only clock the retry policy reads (tests move a fake clock in their _sleep)


def _retry_after(value: str | None) -> float | None:
    """Seconds from a ``Retry-After`` header (the delta-seconds form; an HTTP date is ignored)."""
    try:
        x = float(value) if value else None
    except ValueError:
        return None
    return x if x is not None and 0 <= x < float("inf") else None


class AdaptiveLimiter:
    """Concurrency limit for one service that adapts to rate limiting (AIMD, like TCP congestion control; K11).

    * Additive increase: after ``increase_after`` consecutive successes, +1, up to the configured maximum.
    * Multiplicative decrease: on HTTP 429, halve (minimum 1).

    Requests that were already in flight when the limit was halved belong to the old window; their 429s are the
    same congestion event, so they do not halve again (six parallel 429s: 8 -> 4, not 8 -> 1). Every request
    records the window ("generation") it started in for that purpose. A 5xx, timeout or connection error
    breaks the success streak but does not halve: it says nothing about our request rate.

    * Pause (D43): a 429's Retry-After holds every new request to the service, not only the retry of the
      request that got it. Otherwise the other workers keep sending into the same limit, each earning a 429.
    """

    def __init__(self, name: str, max_limit: int, increase_after: int):
        self.name = name
        self.max_limit = max(1, max_limit)
        self.limit = self.max_limit
        self.increase_after = max(1, increase_after)
        self.in_flight = 0
        self.streak = 0
        self.generation = 0
        self.paused_until = 0.0  # on the _now() clock
        self._cond = threading.Condition()

    def pause(self, seconds: float) -> None:
        """Start no new request for ``seconds`` (a longer pause already running is kept)."""
        with self._cond:
            self.paused_until = max(self.paused_until, _now() + seconds)

    @contextmanager
    def slot(self) -> Iterator[int]:
        """Wait for a free slot and the end of any pause; yields the generation the request starts in."""
        while True:
            with self._cond:
                while self.in_flight >= self.limit:
                    self._cond.wait()
                wait = self.paused_until - _now()
                if wait <= 0:
                    self.in_flight += 1
                    ticket = self.generation
                    break
            _sleep(wait)  # outside the lock; the pause may have grown meanwhile, so check again
        try:
            yield ticket
        finally:
            with self._cond:
                self.in_flight -= 1
                self._cond.notify_all()

    def success(self) -> None:
        with self._cond:
            self.streak += 1
            if self.streak >= self.increase_after:
                self.streak = 0
                if self.limit < self.max_limit:
                    self.limit += 1
                    self._cond.notify_all()

    def failure(self, ticket: int, rate_limited: bool) -> None:
        with self._cond:
            self.streak = 0
            if rate_limited and ticket == self.generation:
                self.generation += 1
                if self.limit > 1:
                    self.limit = max(1, self.limit // 2)
                    log.warning("%s: rate limited (HTTP 429); concurrency lowered to %d", self.name, self.limit)


def call_with_retries(
    fn: Callable[[], T], limiter: AdaptiveLimiter, s: Settings, what: str, error: type[ApiError]
) -> T:
    """Run one attempt per concurrency slot until it succeeds or the retry policy gives up. Same for Bodhan and the LLM.

    429 (rate limited) is the server asking us to slow down, not a failure (D43): the limiter halves and the
    whole service pauses for ``Retry-After`` (capped) or the back-off, and the call tries again. It gives up
    only when waiting would end more than ``s.rate_limit_patience_s`` after its first 429; a check that gives
    up loses its result for the item, so patience is cheaper than a silent gap in the report.
    5xx / 408 / 409 / timeout / connection error: back off (this worker only) without halving, up to
    ``s.max_retries`` attempts. Back-off is exponential with jitter: base * 2^i, scaled into [50 %, 100 %] so
    parallel workers do not retry in lockstep. The slot is released while waiting. Any other error is raised
    at once. Budget is reserved by the caller once per logical call, so retries (not billed) never count twice.
    """
    attempts = max(1, s.max_retries)
    failures, limited = 0, 0
    first_429: float | None = None
    while True:
        try:
            with limiter.slot() as ticket:
                try:
                    value = fn()
                except _Retryable as e:
                    limiter.failure(ticket, e.rate_limited)
                    raise
            limiter.success()
            return value
        except _Retryable as e:
            if e.rate_limited:
                wait = _back_off(s, e.retry_after, limited)
                limited += 1
                now = _now()
                first_429 = now if first_429 is None else first_429
                if now + wait - first_429 > s.rate_limit_patience_s:
                    raise error(
                        0, "retries_exhausted", f"{what}: {e}; still rate limited after {now - first_429:.0f} s"
                    ) from e
                log.warning("%s rate limited (%s); %s paused for %.2fs", what, e, limiter.name, wait)
                limiter.pause(wait)  # the next slot() waits it out, and so does every other worker
                continue
            failures += 1
            if failures >= attempts:
                raise error(0, "retries_exhausted", f"{what}: {e}") from e
            wait = _back_off(s, e.retry_after, failures - 1)
            log.warning("%s failed (%s); retry %d/%d in %.2fs", what, e, failures, attempts - 1, wait)
            _sleep(wait)


def _back_off(s: Settings, retry_after: float | None, i: int) -> float:
    """``Retry-After`` if the server sent one, else base * 2^i jittered into [50 %, 100 %]; capped either way."""
    if retry_after is not None:
        return min(s.retry_max_wait_s, retry_after)
    return min(s.retry_max_wait_s, s.retry_base_s * 2**i) * (0.5 + random.random() / 2)


def wav_duration_s(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        return 30.0  # unknown container: assume the per-request maximum for budgeting


class BodhanClient:
    def __init__(self, settings: Settings, cache: Cache, budget: Budget):
        self.s = settings
        self.cache = cache
        self.budget = budget
        self.costs = defaults()["costs_inr"]
        # one adaptive limit per Bodhan model: keys, and so rate limits, are scoped per model (K11)
        self.limiters = {
            svc: AdaptiveLimiter(svc, settings.concurrency, settings.aimd_increase_after) for svc in KEY_ENV
        }
        self._chat_transport: set[str] = set()  # product routes the gateway answered 404 for (see _text_call)
        self._lock = threading.Lock()
        self._rejected: dict[str, int] = {}  # service -> HTTP status of the auth failure (see _post)

    # ---- plumbing -------------------------------------------------------
    def _key_for(self, service: str) -> str:
        key = {
            "translate": self.s.translate_key,
            "transliterate": self.s.transliterate_key,
            "speak": self.s.speak_key,
            "transcribe": self.s.transcribe_key,
        }[service]
        if not key:
            raise ServiceUnavailable(f"{KEY_ENV[service]} is not set")
        if service in self._rejected:
            raise ServiceUnavailable(
                f"the {service} key was rejected (HTTP {self._rejected[service]}); set {KEY_ENV[service]}"
            )
        return key

    def available(self, service: str) -> bool:
        try:
            self._key_for(service)
            return True
        except ServiceUnavailable:
            return False

    def _post(
        self, service: str, path: str, *, json_body: dict | None = None, files=None, data=None
    ) -> requests.Response:
        url = f"{self.s.bodhan_base_url}{path}"
        headers = {"Authorization": f"Bearer {self._key_for(service)}"}

        def once() -> requests.Response:
            try:
                r = requests.post(
                    url, headers=headers, json=json_body, files=files, data=data, timeout=self.s.request_timeout_s
                )
            except (requests.ConnectionError, requests.Timeout) as e:
                raise _Retryable(type(e).__name__) from e
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = _retry_after(r.headers.get("retry-after"))
                raise _Retryable(f"HTTP {r.status_code}", retry_after, rate_limited=r.status_code == 429)
            if r.status_code >= 400:
                try:
                    err = r.json().get("error", {})
                except ValueError:
                    err = {}
                raise BodhanError(
                    r.status_code, str(err.get("code", r.status_code)), str(err.get("message", r.text[:300]))
                )
            return r

        try:
            return call_with_retries(once, self.limiters[service], self.s, f"{service} {path}", BodhanError)
        except BodhanError as e:
            if e.status in (401, 403):  # the key will not work for this model: stop sending it for the rest of the run
                if service not in self._rejected:
                    log.warning("%s key rejected (HTTP %s); %s calls are off for this run", service, e.status, service)
                self._rejected[service] = e.status
            raise

    def _cached(self, service: str, kind: str, model: str, params: dict, payload: Any, estimate: float, call):
        key = self.cache.key(kind, model, params, payload)
        line = COST_LINE.get(service, service)
        hit = self.cache.get(key)
        if hit is not None:
            value, cost = hit
            self.budget.record_cache_hit(line, cost)
            return value, True
        self._key_for(service)  # fail fast without reserving budget
        self.budget.reserve(line, estimate)
        try:
            value, cost = call()
        except BaseException:
            self.budget.release(estimate)
            raise
        self.budget.settle(line, estimate, cost)
        self.cache.put(key, kind, value, cost)
        return value, False

    def _text_call(self, service: str, route: str, model: str, out_field: str, body: dict) -> tuple[str, bool, int]:
        """(text, truncated, billed output tokens) from a Bodhan text model.

        Uses the documented product route. If the gateway does not serve it (HTTP 404, seen on the hosted API
        on 23 Nov 2026), it switches to the documented chat-completions transport of the same model for the rest
        of the run. The docs state that both transports give identical output, so cached results stay valid.
        """
        if route not in self._chat_transport:
            try:
                j = self._post(service, route, json_body=body).json()
                tokens = int((j.get("usage") or {}).get("completion_tokens", 0))
                return str(j.get(out_field) or ""), bool(j.get("truncated", False)), tokens
            except BodhanError as e:
                if e.status != 404:
                    raise
                with self._lock:  # parallel workers may all meet the 404 first: switch and say so once
                    first = route not in self._chat_transport
                    self._chat_transport.add(route)
                if first:
                    log.warning("%s answered 404; using the chat-completions transport for %s", route, model)
        chat = {"model": model, "messages": [{"role": "user", "content": body["text"]}]}
        chat.update({k: v for k, v in body.items() if k != "text"})
        j = self._post(service, CHAT_PATH, json_body=chat).json()
        choice = (j.get("choices") or [{}])[0]
        text = str((choice.get("message") or {}).get("content") or "")
        tokens = int((j.get("usage") or {}).get("completion_tokens", 0))
        return text, choice.get("finish_reason") == "length", tokens

    # ---- translation ------------------------------------------------------
    def translate(self, text: str, target_language: str) -> dict:
        """Returns {"translation", "truncated", "cached"}. Splits and retries on 413."""
        estimate = max(32, len(text)) * 1.5 * self.costs["translate_per_output_token"]

        def call():
            body = {"text": text, "target_language": target_language}
            out, truncated, tokens = self._text_call("translate", "/translate", TRANSLATE_MODEL, "translation", body)
            return {"translation": out, "truncated": truncated}, tokens * self.costs["translate_per_output_token"]

        try:
            value, cached = self._cached(
                "translate", "translate", TRANSLATE_MODEL, {"target_language": target_language}, text, estimate, call
            )
        except BodhanError as e:
            if e.status == 413 and len(text) > 200:
                return self._translate_in_parts(text, target_language)
            raise
        return {**value, "cached": cached}

    def _translate_in_parts(self, text: str, target_language: str) -> dict:
        parts = re.split(r"(\n\s*\n)", text)  # keep blank-line separators
        if len(parts) == 1:
            mid = len(text) // 2
            cut = text.rfind(". ", 0, mid)
            cut = cut + 2 if cut > 0 else mid
            parts = [text[:cut], text[cut:]]
        out, truncated, cached = [], False, True
        for p in parts:
            if not p.strip():
                out.append(p)
                continue
            r = self.translate(p, target_language)
            out.append(r["translation"])
            truncated |= r["truncated"]
            cached &= r["cached"]
        return {"translation": "".join(out), "truncated": truncated, "cached": cached}

    def transliterate(self, text: str, language: str) -> str:
        estimate = max(16, len(text)) * 2 * self.costs["translate_per_output_token"]

        def call():
            body = {"text": text, "language": language, "script": "native"}
            out, _, tokens = self._text_call(
                "transliterate", "/transliterate", TRANSLITERATE_MODEL, "transliteration", body
            )
            return out, tokens * self.costs["translate_per_output_token"]

        value, _ = self._cached(
            "transliterate", "transliterate", TRANSLITERATE_MODEL, {"language": language}, text, estimate, call
        )
        return value

    # ---- speech -----------------------------------------------------------
    def speak(self, text: str, lang: str, voice: str, style: str | None = None) -> bytes:
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        cost = len(text) * self.costs["speak_per_char"]

        def call():
            body = {
                "model": SPEAK_MODEL,
                "input": text,
                "voice": voice,
                "instructions": json.dumps(instructions, ensure_ascii=False),
            }
            r = self._post("speak", "/v1/audio/speech", json_body=body)
            digest = self.cache.put_blob(r.content, ".wav")
            return {"blob": digest}, cost

        value, _ = self._cached(
            "speak", "speak", SPEAK_MODEL, {"voice": voice, "instructions": instructions}, text, cost, call
        )
        return self.cache.blob_path(value["blob"]).read_bytes()

    def transcribe(self, wav_bytes: bytes, language: str) -> str:
        seconds = wav_duration_s(wav_bytes)
        cost = seconds * self.costs["transcribe_per_second"]

        def call():
            files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
            r = self._post(
                "transcribe",
                "/v1/audio/transcriptions",
                files=files,
                data={"model": TRANSCRIBE_MODEL, "language": language},
            )
            return r.json().get("text", ""), cost

        value, _ = self._cached(
            "transcribe", "transcribe", TRANSCRIBE_MODEL, {"language": language}, bytes_hash(wav_bytes), cost, call
        )
        return value


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

_INDIC_RANGE = re.compile(r"[ऀ-෿؀-ۿꯀ-꯿᱐-᱿]+")


def strip_indic(text: str) -> str:
    """Remove any Indian-script characters from LLM-written prose (belt and braces for the 'LLM never writes Indic' rule)."""
    return _INDIC_RANGE.sub("[…]", text)


def parse_json_object(text: str) -> dict | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


FORMATS = ("json_schema", "json_object", "plain")
# Statuses with which providers refuse a response_format they do not support. OpenRouter answers 404 when no
# provider for the model accepts the requested parameters; others answer 400 or 422.
FORMAT_REFUSED = (400, 404, 422)


@dataclass
class _Answer:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None  # what the provider says it charged (OpenRouter's usage.cost), if it says
    fmt: str = "plain"  # the response format the answer came back in


class LLMClient:
    """OpenAI-compatible chat client for the judge. It only ever asks the model to read and classify.

    Response format (K1): ``json_schema`` (strict) when the caller gives a schema, else ``json_object``,
    else a plain prompt whose answer is parsed for a JSON object. A format the endpoint refuses (HTTP 400,
    404 or 422) is skipped for that model from then on, but only once a lower format has actually worked, so
    an unrelated 400 cannot push a model down to plain prompting for the rest of the run.

    Retries: the SDK's own retries are off (``max_retries=0``; the SDK cannot exempt 429 from them), so every
    attempt goes through ``call_with_retries`` and the ``llm`` limiter, exactly like the Bodhan calls.
    """

    def __init__(
        self,
        settings: Settings,
        cache: Cache,
        budget: Budget,
        inr_per_1k_tokens: float = 0.0,
        extra_body: dict | None = None,
    ):
        self.s = settings
        self.cache = cache
        self.budget = budget
        self.price = inr_per_1k_tokens
        self.extra_body = dict(extra_body or {})  # e.g. {"reasoning": {"effort": "low"}}; off by default
        self.limiter = AdaptiveLimiter("llm", settings.concurrency, settings.aimd_increase_after)
        self._lock = threading.Lock()
        self._refused_below: dict[str, int] = {}  # model -> first FORMATS index worth trying
        self._used: dict[str, str] = {}  # model -> format of its last successful answer
        self._client = None
        if self.available():
            self._client = openai.OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                max_retries=0,
                timeout=settings.request_timeout_s,
            )

    def available(self) -> bool:
        return bool(self.s.llm_model and self.s.llm_api_key)

    @property
    def model(self) -> str:
        return self.s.llm_model or "none"

    def formats_used(self) -> dict[str, str]:
        """Model -> response format of its last answer (reported in run_meta.json)."""
        with self._lock:
            return dict(sorted(self._used.items()))

    def format_for(self, model: str | None = None) -> str | None:
        """The response format the model's last answer came back in (None before its first answer)."""
        return self._used.get(model or self.model)

    def chat_json(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1500,
        schema: dict | None = None,
        model: str | None = None,
        cache_tag: str | None = None,
    ) -> dict | None:
        """Temperature-0 chat call returning a parsed JSON object, or None if the answer is not one.

        Cached by the exact request (model, messages, max_tokens, schema, extra body). ``cache_tag`` goes into
        the cache key only, never into the request: it lets the repeat-consistency check ask the identical
        question again (K2c). An empty answer (a reasoning model that spent all of ``max_tokens`` thinking) is
        asked once more with twice the room; that second request is billed, so it is budgeted on its own.
        """
        if not self.available():
            raise ServiceUnavailable("LLM_MODEL / LLM_API_KEY are not set")
        model = model or self.model
        params: dict[str, Any] = {"temperature": 0, "max_tokens": max_tokens}
        if schema is not None:
            params["schema"] = schema
        if self.extra_body:
            params["extra_body"] = self.extra_body
        if cache_tag:
            params["cache_tag"] = cache_tag
        key = self.cache.key("llm", model, params, messages)
        hit = self.cache.get(key)
        if hit is not None:
            value, cost = hit
            self.budget.record_cache_hit("llm", cost)
            self.budget.add_llm_tokens(value.get("prompt_tokens", 0), value.get("completion_tokens", 0))
            if value.get("format"):  # a cached rerun still reports the format the answer came in
                with self._lock:
                    self._used[model] = value["format"]
            return parse_json_object(value["text"])
        ans = self._budgeted(model, messages, max_tokens, schema)
        cost = self._inr(ans)
        if not ans.text.strip():
            log.info("%s gave no content within %d tokens; asking once more with %d", model, max_tokens, 2 * max_tokens)
            again = self._budgeted(model, messages, 2 * max_tokens, schema)
            cost += self._inr(again)
            ans = _Answer(
                again.text,
                ans.prompt_tokens + again.prompt_tokens,
                ans.completion_tokens + again.completion_tokens,
                None,
                again.fmt,
            )
        value = {
            "text": ans.text,
            "prompt_tokens": ans.prompt_tokens,
            "completion_tokens": ans.completion_tokens,
            "format": ans.fmt,
        }
        self.cache.put(key, "llm", value, cost)
        return parse_json_object(ans.text)

    # ---- one billed request -------------------------------------------------------
    def _inr(self, ans: _Answer) -> float:
        return self.price * (ans.prompt_tokens + ans.completion_tokens) / 1000

    def _budgeted(self, model: str, messages: list[dict], max_tokens: int, schema: dict | None) -> _Answer:
        """Reserve the estimate, make the request (retries included), settle at the token price."""
        estimate = self.price * (sum(len(str(m["content"])) for m in messages) / 3 + max_tokens) / 1000
        self.budget.reserve("llm", estimate)
        try:
            ans = self._complete(model, messages, max_tokens, schema)
        except BaseException:
            self.budget.release(estimate)
            raise
        self.budget.settle("llm", estimate, self._inr(ans))
        self.budget.add_llm_tokens(ans.prompt_tokens, ans.completion_tokens, fresh=True)
        if ans.cost_usd is not None:
            self.budget.add_llm_cost_usd(ans.cost_usd)
        return ans

    def _complete(self, model: str, messages: list[dict], max_tokens: int, schema: dict | None) -> _Answer:
        """Walk the response-format chain from the best format this model has not refused."""
        first = 0 if schema is not None else 1
        start = max(first, self._refused_below.get(model, 0))
        for level in range(start, len(FORMATS)):
            fmt = FORMATS[level]
            try:
                resp = self._request(model, messages, max_tokens, fmt, schema)
            except LLMError as e:
                if e.status in FORMAT_REFUSED and level < len(FORMATS) - 1:
                    log.info("%s: %s refused (HTTP %d); trying %s", model, fmt, e.status, FORMATS[level + 1])
                    continue
                raise
            with self._lock:
                if level > start:  # a better format was refused and this one worked: skip it from now on
                    self._refused_below[model] = max(self._refused_below.get(model, 0), level)
                    log.warning("%s: using response format %s for the rest of the run", model, fmt)
                self._used[model] = fmt
            return _parse_completion(resp, fmt)
        raise AssertionError("unreachable")

    def _request(self, model: str, messages: list[dict], max_tokens: int, fmt: str, schema: dict | None) -> Any:
        if self._client is None:
            raise ServiceUnavailable("LLM not configured (LLM_MODEL / LLM_API_KEY)")
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
        if fmt == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "strict": True, "schema": schema},
            }
        elif fmt == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        client = self._client

        def once() -> Any:
            try:
                return client.chat.completions.create(**kwargs)
            except openai.RateLimitError as e:
                raise _Retryable("HTTP 429", _retry_after(e.response.headers.get("retry-after")), True) from e
            except openai.APIConnectionError as e:  # includes APITimeoutError
                raise _Retryable(type(e).__name__) from e
            except openai.APIStatusError as e:
                if e.status_code in (408, 409) or e.status_code >= 500:
                    retry_after = _retry_after(e.response.headers.get("retry-after"))
                    raise _Retryable(f"HTTP {e.status_code}", retry_after) from e
                code = str(getattr(e, "code", None) or e.status_code)
                raise LLMError(e.status_code, code, str(e.message)[:300]) from e

        return call_with_retries(once, self.limiter, self.s, f"llm {model}", LLMError)


def _parse_completion(resp: Any, fmt: str) -> _Answer:
    choices = getattr(resp, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    text = str(getattr(message, "content", None) or "")
    u = getattr(resp, "usage", None)
    cost = getattr(u, "cost", None)  # OpenRouter adds usage.cost (USD); the SDK keeps unknown fields
    return _Answer(
        text=text,
        prompt_tokens=int(getattr(u, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(u, "completion_tokens", 0) or 0),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        fmt=fmt,
    )


__all__ = [
    "AdaptiveLimiter",
    "ApiError",
    "BodhanClient",
    "LLMClient",
    "LLMError",
    "ServiceUnavailable",
    "BodhanError",
    "BudgetExceeded",
    "strip_indic",
    "parse_json_object",
    "wav_duration_s",
]
