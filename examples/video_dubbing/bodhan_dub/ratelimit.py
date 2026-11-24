"""Client-side pacing for the per-model request quota.

The Bodhan console advertises ``RPM: 120`` for ``indic-transcribe``, and the
server enforces it over a rolling 60-second window. That is the number that
matters — not how many requests are in flight.

Two consequences, both learned the hard way from a live run:

1. **Pace the start of every request.** Firing 72 chunk requests in five
   seconds is ~860 requests/minute, so nearly everything is rejected. Spacing
   request starts at ``60 / RPM`` seconds keeps the pipeline permanently under
   the quota, and a 72-chunk lesson finishes in about 40 seconds instead of
   collapsing.

2. **Back off exponentially and globally.** The first implementation waited a
   fixed 1.5 s. A 60-second window cannot drain in 1.5 s, so the retries were
   themselves keeping the window saturated — a self-sustaining storm that only
   cleared when the burst aged out. Consecutive rejections now double a shared
   cooldown (2s, 4s, 8s … up to a cap) and lower the working rate; a success
   resets both.

Pacing is deliberately conservative: the working rate starts at **half** the
advertised ceiling. The first live runs started at 85% and were rejected on real
audio — 16 uncached calls in eight seconds (about 102/min) tripped the limit — so
the advertised number is treated as an upper bound, not a target. Retries count
against the quota, so headroom is not optional.

Recovery is slow and deliberate: one step every 30 successful calls, and the
rejection streak only clears when that recovery step is taken. A single success
must not reset the backoff, or the pacer oscillates: climb, burst, collapse,
climb again.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import Any

from .log import event

SAFETY_FACTOR = 0.5
DECREASE_FACTOR = 0.85
RECOVERY_EVERY = 30
RECOVERY_STEP = 2
MIN_RPM = 30
MAX_COOLDOWN_S = 12.0


class ConcurrencyGate:
    """Adaptive cap on simultaneous requests.

    The deployed limit behaves like a *parallel-request* cap, not a rate cap:
    runs were rejected at twenty-six calls a minute, far below the advertised
    120. A fixed number of workers therefore cannot be right, so the number of
    in-flight calls is discovered instead.

    One rejection removes one slot; ten successes add one back, up to the
    ceiling. Starting at two is a compromise: it hides latency on a healthy
    account and collapses to one immediately if the account is stricter.
    """

    def __init__(
        self,
        ceiling: int,
        *,
        initial: int = 2,
        recovery_every: int = 10,
        logger: Any = None,
    ) -> None:
        self._ceiling = max(1, ceiling)
        self._limit = max(1, min(initial, self._ceiling))
        self._active = 0
        self._successes = 0
        self._recovery_every = max(1, recovery_every)
        self._stopped = False
        self._cond = threading.Condition()
        self._log = logger
        self.rejections = 0
        self.peaks = self._limit

    def acquire(self) -> bool:
        with self._cond:
            while True:
                if self._stopped:
                    return False
                if self._active < self._limit:
                    self._active += 1
                    return True
                self._cond.wait(0.2)

    def release(self) -> None:
        with self._cond:
            if self._active > 0:
                self._active -= 1
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    def on_throttled(self) -> None:
        with self._cond:
            self.rejections += 1
            self._successes = 0
            if self._limit > 1:
                self._limit -= 1
                self._emit("concurrency.decrease", limit=self._limit)
            self._cond.notify_all()

    def on_success(self) -> None:
        with self._cond:
            self._successes += 1
            if self._successes < self._recovery_every or self._limit >= self._ceiling:
                return
            self._successes = 0
            self._limit += 1
            self.peaks = max(self.peaks, self._limit)
            self._emit("concurrency.increase", limit=self._limit)

    @property
    def limit(self) -> int:
        with self._cond:
            return self._limit

    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {
                "limit": self._limit,
                "ceiling": self._ceiling,
                "peak": self.peaks,
                "rejections": self.rejections,
            }

    def _emit(self, name: str, **fields: Any) -> None:
        if self._log is not None:
            event(self._log, name, stage="ratelimit", **fields)


class RequestPacer:
    def __init__(
        self,
        rpm: int,
        *,
        safety: float = SAFETY_FACTOR,
        min_rpm: int = MIN_RPM,
        base_cooldown_s: float = 2.0,
        max_cooldown_s: float = MAX_COOLDOWN_S,
        logger: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ceiling = max(1, rpm)
        self._effective = max(1, int(self._ceiling * safety))
        self._min_rpm = max(1, min_rpm)
        self._base_cooldown_s = max(0.0, base_cooldown_s)
        self._max_cooldown_s = max(self._base_cooldown_s, max_cooldown_s)
        self._clock = clock
        self._log = logger

        self._cond = threading.Condition()
        self._next_slot = 0.0
        self._cooldown_until = 0.0
        self._streak = 0
        self._successes = 0
        self._stopped = False
        self._last_cooldown_s = 0.0
        self.rejections = 0
        self.waited_s = 0.0

    # -- lifecycle --------------------------------------------------------

    def acquire(self) -> bool:
        """Block until this request is allowed to start. False if stopped."""
        with self._cond:
            while True:
                if self._stopped:
                    return False
                now = self._clock()
                if now < self._cooldown_until:
                    self._wait(min(self._cooldown_until - now, 0.5))
                    continue
                wait = self._next_slot - now
                if wait <= 0:
                    self._next_slot = now + self._interval_s()
                    return True
                self._wait(min(wait, 0.5))

    def _wait(self, timeout: float) -> None:
        started = self._clock()
        self._cond.wait(timeout)
        self.waited_s += max(0.0, self._clock() - started)

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    # -- feedback ---------------------------------------------------------

    def on_success(self) -> None:
        with self._cond:
            self._successes += 1
            if self._successes < RECOVERY_EVERY:
                return
            self._successes = 0
            if self._streak:
                self._streak = 0
                self._emit("ratelimit.recovered")
            if self._effective < self._ceiling:
                self._effective = min(self._ceiling, self._effective + RECOVERY_STEP)
                self._emit("ratelimit.increase", rpm=self._effective)

    def on_rate_limited(self) -> None:
        with self._cond:
            self.rejections += 1
            self._successes = 0
            self._streak += 1
            self._effective = max(self._min_rpm, int(self._effective * DECREASE_FACTOR))
            cooldown = self._base_cooldown_s * (2 ** (self._streak - 1))
            cooldown = min(self._max_cooldown_s, cooldown) * (1.0 + random.random() * 0.2)
            self._last_cooldown_s = cooldown
            self._cooldown_until = max(self._cooldown_until, self._clock() + cooldown)
            self._emit(
                "ratelimit.backoff",
                rpm=self._effective,
                streak=self._streak,
                cooldown_s=round(cooldown, 2),
            )
            self._cond.notify_all()

    def set_ceiling(self, rpm: int) -> None:
        """Adopt the server's advertised request limit."""
        with self._cond:
            rpm = max(1, rpm)
            if rpm == self._ceiling:
                return
            self._ceiling = rpm
            self._effective = min(self._effective, self._ceiling)
            self._emit("ratelimit.ceiling", rpm=self._ceiling, effective=self._effective)
            self._cond.notify_all()

    # -- inspection -------------------------------------------------------

    def _interval_s(self) -> float:
        return 60.0 / max(1, self._effective)

    @property
    def rpm(self) -> int:
        with self._cond:
            return self._effective

    @property
    def ceiling(self) -> int:
        with self._cond:
            return self._ceiling

    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {
                "rpm_ceiling": self._ceiling,
                "rpm_effective": self._effective,
                "interval_s": round(self._interval_s(), 3),
                "rejections": self.rejections,
                "rejection_streak": self._streak,
                "last_cooldown_s": round(self._last_cooldown_s, 2),
                "waited_s": round(self.waited_s, 2),
            }

    def _emit(self, name: str, **fields: Any) -> None:
        if self._log is not None:
            event(self._log, name, stage="ratelimit", **fields)
