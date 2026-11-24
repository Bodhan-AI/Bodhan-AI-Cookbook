"""retry: Retries with exponential backoff and jitter, honouring Retry-After.
cache: Content-addressed SQLite cache for every model output, plus a persistent spend log.
cost: INR cost ledger with a reserve/commit budget gate.
spend: Print the real Bodhan spend recorded in a cache: ``python -m bodhan_eval.infra``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import random
import sqlite3
import statistics
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx

from .config import Prices

# -------------------- retry
T = TypeVar("T")


log = logging.getLogger("bodhan_eval.infra")


RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds to wait from a Retry-After header: either delta-seconds ("42", what
    Bodhan sends) or an HTTP date. None when absent or unparseable."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = time.time() if now is None else now
    return max(0.0, when.timestamp() - current)


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    extra_retryable: tuple[type[BaseException], ...] = (),
) -> T:
    retryable = (RetryableError, httpx.TransportError, httpx.TimeoutException, *extra_retryable)
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retryable as exc:
            if attempt == attempts:
                log.warning("giving up", extra={"fields": {"what": what, "attempts": attempts, "error": repr(exc)}})
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1))
            delay = delay * (0.5 + random.random() / 2)  # jitter, timing only
            ra = getattr(exc, "retry_after", None)
            if ra is not None:
                delay = max(delay, min(ra, max_delay * 2))
            log.info("retrying", extra={"fields": {"what": what, "attempt": attempt, "delay_s": round(delay, 2), "error": repr(exc)}})
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


# -------------------- cache
def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_key(model: str, endpoint: str, params: dict, payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    envelope = {"model": model, "endpoint": endpoint, "params": params, "input_sha256": sha256_bytes(payload)}
    return sha256_bytes(canonical_json(envelope).encode("utf-8"))


@dataclass
class CacheEntry:
    key: str
    kind: str
    meta: dict
    payload: bytes


class Cache:
    """Thread-safe SQLite cache. ``read=False`` forces fresh calls but still writes."""

    def __init__(self, path: str | Path, *, read: bool = True, write: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.read_enabled = read
        self.write_enabled = write
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, kind TEXT NOT NULL, meta TEXT NOT NULL, payload BLOB NOT NULL, created REAL NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS spend (id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT NOT NULL, "
            "units REAL NOT NULL, inr REAL NOT NULL, created REAL NOT NULL)"
        )

    # ---- model output cache -------------------------------------------------
    def get(self, key: str) -> CacheEntry | None:
        if not self.read_enabled:
            self.misses += 1
            return None
        with self._lock:
            row = self._conn.execute("SELECT kind, meta, payload FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        return CacheEntry(key=key, kind=row[0], meta=json.loads(row[1]), payload=bytes(row[2]))

    def put(self, key: str, kind: str, meta: dict, payload: bytes = b"") -> None:
        if not self.write_enabled:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, kind, meta, payload, created) VALUES (?, ?, ?, ?, ?)",
                (key, kind, canonical_json(meta), sqlite3.Binary(payload), time.time()),
            )

    # ---- persistent spend log (real money only) -----------------------------
    def add_spend(self, service: str, units: float, inr: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spend (service, units, inr, created) VALUES (?, ?, ?, ?)",
                (service, float(units), float(inr), time.time()),
            )

    def lifetime_spend(self) -> dict[str, float]:
        with self._lock:
            rows = self._conn.execute("SELECT service, SUM(inr) FROM spend GROUP BY service").fetchall()
        return {service: float(total or 0.0) for service, total in rows}

    def stats(self) -> dict:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
        return {"entries": int(n), "hits": self.hits, "misses": self.misses}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# -------------------- cost
SERVICES = ("translate", "speak", "transcribe", "llm")


class BudgetExceeded(Exception):
    """Raised before a paid call that would push spend past a cap."""


@dataclass
class Reservation:
    service: str
    inr: float


class CostLedger:
    def __init__(
        self,
        prices: Prices,
        *,
        max_run_inr: float | None = None,
        lifetime_cap_inr: float | None = None,
        lifetime_spent_at_start: float = 0.0,
        spend_sink=None,  # callable(service, units, inr), e.g. Cache.add_spend
    ):
        self.prices = prices
        self.max_run_inr = max_run_inr
        self.lifetime_cap_inr = lifetime_cap_inr
        self.lifetime_spent_at_start = lifetime_spent_at_start
        self._sink = spend_sink
        self._lock = threading.Lock()
        self._reserved = 0.0
        self.new_inr: dict[str, float] = defaultdict(float)
        self.cached_inr: dict[str, float] = defaultdict(float)
        self.new_units: dict[str, float] = defaultdict(float)
        self.cached_units: dict[str, float] = defaultdict(float)
        self.new_calls: dict[str, int] = defaultdict(int)
        self.cached_calls: dict[str, int] = defaultdict(int)
        self.skipped_budget: dict[str, int] = defaultdict(int)

    # ---- pricing --------------------------------------------------------------
    def price(self, service: str, units: float) -> float:
        p = self.prices
        if service == "translate":  # units = output tokens
            return units / 10_000 * p.translate_per_10k_output_tokens
        if service == "speak":  # units = input characters
            return units / 10_000 * p.speak_per_10k_input_chars
        if service == "transcribe":  # units = audio seconds, billed per second
            return math.ceil(units) / 60 * p.transcribe_per_audio_minute
        if service == "llm":  # LLM price is provider-specific; tokens are tracked, INR is not
            return 0.0
        raise ValueError(f"unknown service {service!r}")

    # ---- budget gate ----------------------------------------------------------
    @property
    def run_new_total(self) -> float:
        return sum(self.new_inr.values())

    def reserve(self, service: str, estimated_inr: float) -> Reservation:
        with self._lock:
            projected_run = self.run_new_total + self._reserved + estimated_inr
            if self.max_run_inr is not None and projected_run > self.max_run_inr + 1e-12:
                self.skipped_budget[service] += 1
                raise BudgetExceeded(
                    f"{service}: estimated ₹{estimated_inr:.4f} would exceed --max-cost-inr ₹{self.max_run_inr:.2f} "
                    f"(spent ₹{self.run_new_total:.4f}, in flight ₹{self._reserved:.4f})"
                )
            if self.lifetime_cap_inr is not None:
                projected_life = self.lifetime_spent_at_start + projected_run
                if projected_life > self.lifetime_cap_inr + 1e-12:
                    self.skipped_budget[service] += 1
                    raise BudgetExceeded(
                        f"{service}: lifetime spend would reach ₹{projected_life:.4f}, above --lifetime-cap-inr ₹{self.lifetime_cap_inr:.2f}"
                    )
            self._reserved += estimated_inr
            return Reservation(service, estimated_inr)

    def commit(self, reservation: Reservation, units: float) -> float:
        actual = self.price(reservation.service, units)
        with self._lock:
            self._reserved -= reservation.inr
            self.new_inr[reservation.service] += actual
            self.new_units[reservation.service] += units
            self.new_calls[reservation.service] += 1
        if self._sink is not None and actual > 0:
            self._sink(reservation.service, units, actual)
        return actual

    def release(self, reservation: Reservation) -> None:
        """Call failed: free the reservation without charging."""
        with self._lock:
            self._reserved -= reservation.inr

    def record_cached(self, service: str, units: float) -> None:
        with self._lock:
            self.cached_inr[service] += self.price(service, units)
            self.cached_units[service] += units
            self.cached_calls[service] += 1

    def record_free(self, service: str, units: float) -> None:
        """Uncached calls that have no INR price (the LLM): count tokens and calls only."""
        with self._lock:
            self.new_units[service] += units
            self.new_calls[service] += 1

    # ---- reporting ------------------------------------------------------------
    def summary(self) -> dict:
        per_service = {}
        for s in SERVICES:
            per_service[s] = {
                "new_calls": self.new_calls[s],
                "cached_calls": self.cached_calls[s],
                "new_units": round(self.new_units[s], 3),
                "cached_units": round(self.cached_units[s], 3),
                "new_inr": round(self.new_inr[s], 6),
                "cached_equivalent_inr": round(self.cached_inr[s], 6),
                "total_equivalent_inr": round(self.new_inr[s] + self.cached_inr[s], 6),
                "skipped_budget": self.skipped_budget[s],
            }
        return {
            "per_service": per_service,
            "new_inr": round(self.run_new_total, 6),
            "cached_equivalent_inr": round(sum(self.cached_inr.values()), 6),
            "total_equivalent_inr": round(self.run_new_total + sum(self.cached_inr.values()), 6),
            "units": {"translate": "output_tokens", "speak": "input_chars", "transcribe": "audio_seconds", "llm": "tokens"},
        }


# -------------------- spend
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m bodhan_eval.infra")
    p.add_argument("--cache-dir", type=Path, default=Path(".bodhan_cache"))
    args = p.parse_args(argv)
    path = args.cache_dir / "cache.sqlite"
    if not path.exists():
        print(f"no cache at {path}: INR 0.0000 spent")
        return 0
    cache = Cache(path)
    per = cache.lifetime_spend()
    for service in sorted(per):
        print(f"{service:<11} INR {per[service]:.4f}")
    print(f"{'TOTAL':<11} INR {sum(per.values()):.4f}")
    print(f"cache entries: {cache.stats()['entries']}")
    cache.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# -------------------- stats
def robust_z(x: float, values: list[float], *, min_scale: float = 1e-6) -> float | None:
    if len(values) < 3:
        return None
    med = statistics.median(values)
    mad = statistics.median(abs(v - med) for v in values) * 1.4826
    scale = max(mad, abs(med) * 0.02, min_scale)  # avoid a near-zero scale when values are identical
    return (x - med) / scale


class RunStats:
    def __init__(self) -> None:
        self._series: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))

    def add(self, series: str, lang: str, item_id: str, value: float | None) -> None:
        if value is not None and math.isfinite(value):
            self._series[series][lang][item_id] = float(value)

    def values(self, series: str, lang: str, exclude: str | None = None) -> list[float]:
        return [v for k, v in self._series[series][lang].items() if k != exclude]

    def z(self, series: str, lang: str, item_id: str, *, min_n: int) -> float | None:
        own = self._series[series][lang].get(item_id)
        others = self.values(series, lang, exclude=item_id)
        if own is None or len(others) < min_n:
            return None
        return robust_z(own, others)

    def median(self, series: str, lang: str, exclude: str | None = None) -> float | None:
        vals = self.values(series, lang, exclude)
        return statistics.median(vals) if vals else None

    def summary(self) -> dict:
        out: dict = {}
        for series, by_lang in sorted(self._series.items()):
            out[series] = {}
            for lang, vals in sorted(by_lang.items()):
                v = list(vals.values())
                out[series][lang] = {"n": len(v), "median": round(statistics.median(v), 4) if v else None}
        return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% score interval for k successes out of n (n=0 -> the uninformative (0, 1))."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    w = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - w) / d), min(1.0, (c + w) / d))


def rate(k: int, n: int) -> dict:
    """A rate with its Wilson interval, ready for the report (value None when n=0)."""
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "value": (k / n) if n else None, "ci95": [lo, hi]}


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on the discordant pairs of a paired comparison:
    b = right before / wrong now, c = wrong before / right now (binomial test, p = 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
