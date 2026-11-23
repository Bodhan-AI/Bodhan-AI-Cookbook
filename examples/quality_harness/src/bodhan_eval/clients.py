"""Model clients: every Bodhan and LLM call goes through here, behind one cache and one cost ledger.

- Cache: SQLite, keyed by a hash of the endpoint, model and exact input. A rerun is served from it
  at no cost, which is also what makes reruns deterministic.
- Retries: the openai package retries timeouts, 408/409/429 and 5xx with exponential backoff and
  honours `retry-after`. On top of that, LLM replies with no content or no JSON are retried here.
- Concurrency: a cap on parallel requests per model, plus a requests-per-minute pace for Bodhan.
- Cost: every paid call reserves its estimated cost against --max-cost-inr before it is sent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import openai
from openai import OpenAI

from .config import Config

if TYPE_CHECKING:
    import httpx2  # the openai package's HTTP library; only tests pass a client

log = logging.getLogger("bodhan_eval.clients")

# Hosted prices from the brief, in INR.
INR_PER_TRANSLATE_TOKEN = 0.20 / 10_000  # per output token
INR_PER_SPEAK_CHAR = 6 / 10_000  # per input character
INR_PER_AUDIO_MINUTE = 0.10

# Bodhan allows 10 parallel requests and 120 requests/minute per key (docs/bodhan_api.md).
# NVIDIA sends no rate-limit headers, so the LLM gets a modest cap and relies on 429 backoff.
LIMITS = {"translate": (8, 120), "speak": (8, 120), "transcribe": (8, 120), "llm": (4, None)}
# What we actually got per model on our keys (2026-11; 429s above these): for runtime projections.
OBSERVED_RPM = {"translate": 12, "speak": 7, "transcribe": 8, "llm": 8}


def limits(env=None) -> dict[str, tuple[int, int | None]]:
    """LIMITS, each overridable as LIMIT_<MODEL>=<parallel>/<per minute>, e.g. LIMIT_LLM=16/240."""
    env = os.environ if env is None else env
    out = dict(LIMITS)
    for model in out:
        raw = env.get(f"LIMIT_{model.upper()}")
        if raw:
            par, _, rpm = raw.partition("/")
            out[model] = (int(par), int(rpm) if rpm.strip() else None)
    return out


def expected_rpm(env=None) -> dict[str, int]:
    """Requests per minute to plan with: a LIMIT_<MODEL> setting if given, else what we observed."""
    return {m: (lim[1] or OBSERVED_RPM[m]) if f"LIMIT_{m.upper()}" in (os.environ if env is None else env) else OBSERVED_RPM[m]
            for m, lim in limits(env).items()}
SDK_RETRIES = 4
# Bodhan also has an unpublished per-model limit ("Maximum rate limit reached for the model", retry-after
# about a minute). After the SDK's own retries, wait for it, all threads together, this many more times.
RATE_LIMIT_ROUNDS = 6
LLM_RETRIES = 4  # extra attempts when the reply has no content or no JSON (seen: empty replies 3 times in a row)
LLM_MAX_TOKENS = 4096  # reasoning models spend 800-1,600 tokens thinking before they answer
DEFAULT_VOICE = "Kavya"  # Bodhan: any voice can read any language


class ModelError(Exception):
    """A model call failed after retries. Callers mark that check as `error` for the item."""


class BudgetExceeded(ModelError):
    """The call was not sent because it would exceed --max-cost-inr."""


@dataclass
class Translation:
    text: str
    finish_reason: str  # anything but "stop" means the output was cut off


@dataclass
class _Stats:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    skipped_budget: int = 0
    cost_inr: float = 0.0
    cached_cost_inr: float = 0.0  # what the cache hits would have cost
    tokens: int = 0  # LLM only: prompt + completion


@dataclass
class Ledger:
    """Spend per model, and the --max-cost-inr check. Thread-safe."""

    cap_inr: float | None = None
    stats: dict[str, _Stats] = field(default_factory=dict)
    _reserved: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _s(self, model: str) -> _Stats:
        return self.stats.setdefault(model, _Stats())

    @property
    def spent_inr(self) -> float:
        return sum(s.cost_inr for s in self.stats.values())

    def reserve(self, model: str, estimate: float) -> None:
        with self._lock:
            if self.cap_inr is not None and self.spent_inr + self._reserved + estimate > self.cap_inr:
                self._s(model).skipped_budget += 1
                raise BudgetExceeded(f"{model}: ₹{estimate:.4f} would exceed the ₹{self.cap_inr} budget")
            self._reserved += estimate

    def settle(self, model: str, estimate: float, cost: float | None) -> None:
        """Replace the reservation with the real cost; cost=None means the call failed."""
        with self._lock:
            self._reserved -= estimate
            s = self._s(model)
            if cost is None:
                s.failures += 1
            else:
                s.calls += 1
                s.cost_inr += cost

    def hit(self, model: str, cost: float) -> None:
        with self._lock:
            s = self._s(model)
            s.cache_hits += 1
            s.cached_cost_inr += cost

    def add_tokens(self, model: str, n: int) -> None:
        with self._lock:
            self._s(model).tokens += n

    def summary(self) -> dict:
        return {
            "budget_inr": self.cap_inr,
            "spent_inr": round(self.spent_inr, 4),
            "by_model": {m: {k: round(v, 4) if isinstance(v, float) else v for k, v in vars(s).items()}
                         for m, s in sorted(self.stats.items())},
            "note": "LLM cost is not priced; its tokens are reported instead",
        }


class Cache:
    """SQLite key -> JSON value; audio is stored as files next to it."""

    def __init__(self, root: Path):
        self.root = root
        (root / "audio").mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(root / "cache.sqlite", check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, model TEXT, value TEXT, created REAL)")
        self._lock = threading.Lock()

    @staticmethod
    def key(*parts: object) -> str:
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()
        return hashlib.sha256(blob).hexdigest()

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, model: str, value: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)",
                             (key, model, json.dumps(value, ensure_ascii=False), time.time()))

    def close(self) -> None:
        self._db.close()


class _Limiter:
    """At most `parallel` calls at once, and call starts spaced to stay under `per_minute`.

    On a rate limit everyone pauses and the spacing doubles; it recovers 10% per 20 successful calls.
    """

    def __init__(self, parallel: int, per_minute: int | None):
        self._sem = threading.BoundedSemaphore(parallel)
        self._base = self._gap = 60 / per_minute if per_minute else 0.0
        self._next = 0.0
        self._ok = 0
        self._lock = threading.Lock()

    def rate_limited(self, wait: float) -> None:
        with self._lock:
            self._next = max(self._next, time.monotonic() + wait)
            self._gap = min(max(self._gap * 2, 1.0), 10.0)  # at least 1 s apart, at most 10 s
            self._ok = 0

    def succeeded(self) -> None:
        with self._lock:
            self._ok += 1
            if self._ok >= 20 and self._gap > self._base:
                self._gap, self._ok = max(self._base, self._gap * 0.9), 0

    def __enter__(self):
        self._sem.acquire()
        with self._lock:
            now = time.monotonic()
            wait, self._next = max(0.0, self._next - now), max(now, self._next) + self._gap
        if wait:
            time.sleep(wait)

    def __exit__(self, *exc):
        self._sem.release()


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError, OSError):
        return None


def parse_json_object(text: str) -> dict | None:
    """The first complete JSON object in a reply, ignoring prose, code fences or trailing junk."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


class Clients:
    """One OpenAI-compatible client per model; models without credentials are simply absent."""

    def __init__(self, cfg: Config, cache_dir: Path, http_client: "httpx2.Client | None" = None):
        self.cache = Cache(cache_dir)
        self.ledger = Ledger(cfg.max_cost_inr)
        self._limits = {m: _Limiter(*lim) for m, lim in limits().items()}
        self._clients: dict[str, OpenAI] = {}
        self._ids: dict[str, str] = {}  # identifies the model in cache keys (never includes a key)
        common = {"max_retries": SDK_RETRIES, "timeout": 120.0, "http_client": http_client}
        for model, key in cfg.bodhan_keys.items():
            self._clients[model] = OpenAI(api_key=key, base_url=cfg.bodhan_base_url, **common)
            self._ids[model] = f"{cfg.bodhan_base_url}|indic-{model}"
        if cfg.llm_enabled:
            self._clients["llm"] = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url, **common)
            self._ids["llm"] = f"{cfg.llm_base_url}|{cfg.llm_model}"
        self._llm_model = cfg.llm_model

    def available(self, model: str) -> bool:
        return model in self._clients

    def close(self) -> None:
        self.cache.close()

    # ponytail: two threads missing the cache on the same key both pay; add per-key locks if a
    # pipeline stage ever issues duplicate calls concurrently.
    def _call(self, model: str, params: dict, estimate: float, send) -> dict:
        """Cache lookup, budget reservation, rate limit, call. `send()` returns (value, cost)."""
        if model not in self._clients:
            raise ModelError(f"{model}: no credentials configured")
        key = Cache.key(self._ids[model], params)
        if (cached := self.cache.get(key)) is not None:
            self.ledger.hit(model, cached.get("cost_inr", 0.0))
            return cached
        self.ledger.reserve(model, estimate)
        limiter = self._limits[model]
        try:
            for round_ in range(RATE_LIMIT_ROUNDS + 1):
                try:
                    with limiter:
                        value, cost = send(key)
                    limiter.succeeded()
                    break
                except openai.RateLimitError as e:
                    if round_ == RATE_LIMIT_ROUNDS or "credit" in str(e).lower():
                        raise  # out of credit is also a 429: waiting won't help
                    wait = _retry_after(e)
                    log.info("%s rate-limited; all %s calls pause %.0f s (round %d/%d)", model, model, wait,
                             round_ + 1, RATE_LIMIT_ROUNDS)
                    limiter.rate_limited(wait)
        except openai.OpenAIError as e:
            self.ledger.settle(model, estimate, None)
            log.warning("%s call failed: %s", model, _describe(e))
            raise ModelError(f"{model}: {_describe(e)}") from e
        except ModelError:
            self.ledger.settle(model, estimate, None)
            raise
        self.ledger.settle(model, estimate, cost)
        value["cost_inr"] = cost
        self.cache.put(key, model, value)
        return value

    def translate(self, text: str, lang: str) -> Translation:
        """Indic-Translate into `lang`; lang="en" is a back-translation. The source language is detected."""
        def send(_key):
            r = self._clients["translate"].chat.completions.create(
                model="indic-translate",
                messages=[{"role": "user", "content": text}],
                extra_body={"target_language_code": lang},  # omitted, Bodhan defaults to Hindi
            )
            tokens = r.usage.completion_tokens if r.usage else _approx_tokens(text)
            choice = r.choices[0]
            return {"text": choice.message.content or "", "finish_reason": choice.finish_reason}, tokens * INR_PER_TRANSLATE_TOKEN

        v = self._call("translate", {"text": text, "lang": lang}, _approx_tokens(text) * INR_PER_TRANSLATE_TOKEN, send)
        return Translation(v["text"], v["finish_reason"])

    def speak(self, text: str, lang: str, voice: str = DEFAULT_VOICE) -> Path:
        """Indic-Speak audio for one chunk of text (keep it to about 30 s of speech). Returns a WAV path."""
        cost = len(text) * INR_PER_SPEAK_CHAR

        def send(key):
            r = self._clients["speak"].audio.speech.create(
                model="indic-speak", input=text, voice=voice, instructions=json.dumps({"lang": lang}),
            )
            path = self.cache.root / "audio" / f"{key}.wav"  # Bodhan sends WAV under an audio/mpeg header
            path.write_bytes(r.content)
            return {"file": path.name}, cost

        v = self._call("speak", {"text": text, "lang": lang, "voice": voice}, cost, send)
        return self.cache.root / "audio" / v["file"]

    def transcribe(self, audio: Path, lang: str) -> str:
        """Indic-Transcribe one clip of at most 30 s. Returns text only: the API gives no timestamps."""
        data = audio.read_bytes()
        seconds = wav_seconds(audio)
        # ponytail: non-WAV input is estimated at the 30 s request maximum; decode it if this is ever off by much.
        cost = (seconds if seconds is not None else 30.0) / 60 * INR_PER_AUDIO_MINUTE

        def send(_key):
            r = self._clients["transcribe"].audio.transcriptions.create(
                model="indic-transcribe", file=(audio.name, data), language=lang,
            )
            return {"text": r.text}, cost

        params = {"audio_sha256": hashlib.sha256(data).hexdigest(), "lang": lang}
        return self._call("transcribe", params, cost, send)["text"]

    def llm(self, messages: list[dict], repeat: int = 0) -> dict:
        """The LLM's reply as a JSON object. No temperature/seed/response_format: providers disagree on them.
        `repeat` > 0 asks again instead of reusing the cached reply (to measure how consistent the LLM is)."""
        def send(_key):
            for attempt in range(LLM_RETRIES + 1):
                r = self._clients["llm"].chat.completions.create(
                    model=self._llm_model, messages=messages, max_tokens=LLM_MAX_TOKENS,
                )
                if r.usage:
                    self.ledger.add_tokens("llm", r.usage.total_tokens)
                content = r.choices[0].message.content or ""
                if parse_json_object(content) is not None:
                    return {"content": content}, 0.0
                log.info("llm reply %d/%d unusable (finish_reason=%s, %d chars), retrying",
                         attempt + 1, LLM_RETRIES + 1, r.choices[0].finish_reason, len(content))
            raise ModelError("llm: no JSON object in the reply after retries")

        params = {"messages": messages, **({"repeat": repeat} if repeat else {})}  # repeat 0 keeps existing cache keys
        content = self._call("llm", params, 0.0, send)["content"]
        return parse_json_object(content)  # type: ignore[return-value]  # cached replies always parsed


def _retry_after(e: openai.RateLimitError) -> float:
    try:
        return min(120.0, max(1.0, float(e.response.headers.get("retry-after", 30))))
    except (TypeError, ValueError):
        return 30.0


def _approx_tokens(text: str) -> int:
    return max(16, len(text))  # generous: Indic output is rarely more than one token per character


def _describe(e: openai.OpenAIError) -> str:
    if isinstance(e, openai.APIStatusError):
        return f"HTTP {e.status_code}: {e.message}"
    return f"{type(e).__name__}: {e}"
