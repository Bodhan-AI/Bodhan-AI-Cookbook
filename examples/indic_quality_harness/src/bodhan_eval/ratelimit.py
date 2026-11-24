"""Pacing, bounded back-off on HTTP 429, and a circuit breaker.

The Cookbook guideline is "handle 429 by backing off; don't loop-retry".
So a call that hits 429 is retried after a few increasing delays and then
given up. Once a call has exhausted its back-off, the limiter *trips*: every
later call fails immediately (and is recorded per item) instead of sending
more requests that will be refused. A new run starts with a fresh limiter,
and everything already done is in the cache.
"""

from __future__ import annotations

import time


class RateLimitedError(RuntimeError):
    """The provider kept returning 429 after back-off, or the breaker is open."""


def is_rate_limit(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 429 or "429" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class RateLimiter:
    def __init__(
        self,
        name: str,
        min_interval: float = 0.0,
        backoff: tuple[float, ...] = (10.0, 30.0, 60.0),
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.name = name
        self.min_interval = min_interval
        self.backoff = backoff
        self.sleep = sleep
        self.clock = clock
        self.tripped = False
        self.rate_limited_responses = 0
        self._last_call = None

    def _pace(self) -> None:
        if self._last_call is not None and self.min_interval > 0:
            wait = self.min_interval - (self.clock() - self._last_call)
            if wait > 0:
                self.sleep(wait)
        self._last_call = self.clock()

    def call(self, fn, *args, **kwargs):
        if self.tripped:
            raise RateLimitedError(
                f"{self.name}: skipped, the provider rate limit persisted earlier in this run"
            )

        delays = list(self.backoff) + [None]
        for delay in delays:
            self._pace()
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - re-raised unless 429
                if not is_rate_limit(exc):
                    raise
                self.rate_limited_responses += 1
                if delay is None:
                    self.tripped = True
                    raise RateLimitedError(
                        f"{self.name}: still rate limited (429) after back-off; "
                        "skipping remaining calls in this run"
                    ) from exc
                print(f"{self.name}: rate limited (429), backing off {delay:.0f}s")
                self.sleep(delay)

    def stats(self) -> dict:
        return {"rate_limited_responses": self.rate_limited_responses, "tripped": self.tripped,
                "min_interval_seconds": self.min_interval}
