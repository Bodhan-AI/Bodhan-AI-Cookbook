"""Minimal OpenAI-compatible chat client (plain chat messages only, no provider-specific features).

Pacing: if the provider sends token rate-limit headers (Groq does; verified 2026-11-20:
x-ratelimit-limit-tokens, x-ratelimit-remaining-tokens, x-ratelimit-reset-tokens), all threads share one
pacer. Before a call it estimates how many tokens the call needs, and if fewer are left it waits for the bucket
to refill instead of sending into a 429. The refill rate is read from the headers: (limit - remaining) / reset.
Without such headers nothing is paced.

Errors: 429 and 5xx are retried with waits taken from the headers (or backoff). A daily request cap
(x-ratelimit-remaining-requests: 0, or a 429 that says "per day") is not retried, because it lasts for hours:
LLMUnavailable is raised at once and for every later call. Callers treat an LLM error as "no answer", never
as a "NO" answer.
"""

from __future__ import annotations

import logging
import re
import threading
import time

import requests

from .api import post_with_retry
from .config import get_llm_config

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
MAX_WAIT = 65.0          # seconds; never sleep longer than this in one go
STATS = {"calls": 0, "ok": 0, "rate_limited": 0, "paced_s": 0.0, "errors": 0, "prompt_tokens": 0,
         "completion_tokens": 0}
# Measured 2026-11-20: Groq's free tier also caps gpt-oss-120b at 200,000 tokens per day (a 429 that says
# "tokens per day (TPD)"). About 12 test runs of 75-90 s used it up, so the report counts tokens per run.


class LLMUnavailable(RuntimeError):
    """The LLM cannot be used for the rest of this run (e.g. its daily request cap is reached)."""


_unavailable: dict = {}


def parse_duration(s: str | None) -> float | None:
    """'585ms' -> 0.585, '7.66s' -> 7.66, '1m2.5s' -> 62.5, '5h25m26.4s' -> 19526.4. None if not a duration."""
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?", s)
    if not m or not any(m.groups()):
        try:
            return float(s)
        except ValueError:
            return None
    h, mi, sec, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mi * 60 + sec + ms / 1000


class Pacer:
    """Token bucket mirrored from the provider's headers, shared by all threads."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.lock = threading.Lock()
        self.clock, self.sleep = clock, sleep
        self.limit = self.remaining = self.rate = None
        self.t_obs = 0.0
        self.out_est = 400.0        # expected output tokens per call (reasoning included); learned from usage

    def need(self, messages: list[dict]) -> int:
        prompt = sum(len(m.get("content") or "") for m in messages) / 3.5
        n = int(prompt + self.out_est)
        return min(n, int(self.limit)) if self.limit else n

    def _avail(self, now: float) -> float:
        return min(self.limit, self.remaining + self.rate * (now - self.t_obs))

    def acquire(self, need: int) -> float:
        """Block until `need` tokens should be available, then reserve them. Returns seconds waited."""
        waited = 0.0
        while True:
            with self.lock:
                if self.rate is None or not self.limit:
                    return waited
                now = self.clock()
                a = self._avail(now)
                if a >= need:
                    self.remaining, self.t_obs = a - need, now
                    return waited
                wait = min(MAX_WAIT, (need - a) / self.rate + 0.05)
            self.sleep(wait)
            waited += wait

    def update(self, headers, completion_tokens: int | None = None) -> None:
        h = {k.lower(): v for k, v in (headers or {}).items()}
        try:
            limit = float(h["x-ratelimit-limit-tokens"])
            remaining = float(h["x-ratelimit-remaining-tokens"])
        except (KeyError, ValueError):
            return
        reset = parse_duration(h.get("x-ratelimit-reset-tokens"))
        with self.lock:
            self.limit, self.remaining, self.t_obs = limit, remaining, self.clock()
            if reset and reset > 0 and limit > remaining:
                self.rate = (limit - remaining) / reset
            elif self.rate is None:
                self.rate = limit / 60.0            # no refill information yet: assume a per-minute window
            if completion_tokens:
                self.out_est = max(100.0, 0.7 * self.out_est + 0.3 * completion_tokens)

    def wait_after_429(self, headers, attempt: int) -> float:
        h = {k.lower(): v for k, v in (headers or {}).items()}
        for key in ("retry-after", "x-ratelimit-reset-tokens"):
            d = parse_duration(h.get(key))
            if d is not None and d > 0:
                return min(MAX_WAIT, d + 0.25)
        return min(MAX_WAIT, 2.0 * 2 ** attempt)


_PACERS: dict[str, Pacer] = {}      # one per model: providers limit each model separately
_PACERS_LOCK = threading.Lock()
_STATS_LOCK = threading.Lock()


def reset_stats() -> None:
    with _STATS_LOCK:
        for k in STATS:
            STATS[k] = 0.0 if k == "paced_s" else 0


def unavailable_reason() -> str | None:
    return _unavailable.get("why")


def pacer_for(model: str) -> Pacer:
    with _PACERS_LOCK:
        return _PACERS.setdefault(model, Pacer())


def _count(key: str, n: float = 1) -> None:
    with _STATS_LOCK:
        STATS[key] += n


def _daily_cap(r) -> bool:
    h = {k.lower(): v for k, v in (r.headers or {}).items()}
    if str(h.get("x-ratelimit-remaining-requests", "")).strip() == "0":
        return True
    t = (r.text or "").lower()
    return "per day" in t or "(rpd)" in t or "(tpd)" in t


def chat(messages: list[dict], *, temperature: float = 0.0, max_tokens: int = 8000, model: str | None = None) -> str:
    """Send chat messages, return the assistant text. `model` overrides the configured model.
    Raises RuntimeError when no answer could be obtained, LLMUnavailable when the LLM is out for this run."""
    cfg = get_llm_config()
    if _unavailable:
        raise LLMUnavailable(_unavailable["why"])
    url = f"{cfg['base_url']}/chat/completions"
    name = model or cfg["model"]
    pacer = pacer_for(name)
    body = {"model": name, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    _count("calls")
    last = "no attempt"
    for attempt in range(MAX_ATTEMPTS):
        _count("paced_s", pacer.acquire(pacer.need(messages)))
        try:
            r = post_with_retry(url, cfg["key"], max_attempts=1, json=body)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(min(MAX_WAIT, 2.0 * 2 ** attempt))
            continue
        usage = (r.json().get("usage") or {}) if r.ok else {}
        pacer.update(r.headers, usage.get("completion_tokens"))
        _count("prompt_tokens", usage.get("prompt_tokens") or 0)
        _count("completion_tokens", usage.get("completion_tokens") or 0)
        if r.status_code == 429:
            _count("rate_limited")
            if _daily_cap(r):
                _unavailable["why"] = f"LLM daily request cap reached: {r.text[:200]}"
                log.error("llm: %s", _unavailable["why"])
                _count("errors")
                raise LLMUnavailable(_unavailable["why"])
            wait = pacer.wait_after_429(r.headers, attempt)
            last = f"HTTP 429 (waited {wait:.1f}s)"
            log.warning("llm: rate limited, waiting %.1fs (attempt %d/%d)", wait, attempt + 1, MAX_ATTEMPTS)
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            time.sleep(min(MAX_WAIT, 2.0 * 2 ** attempt))
            continue
        if not r.ok:
            _count("errors")
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:200]}")
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        _count("ok")
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    _count("errors")
    raise RuntimeError(f"LLM gave no answer after {MAX_ATTEMPTS} attempts ({last})")
