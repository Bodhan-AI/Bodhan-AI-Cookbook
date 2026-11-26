"""Client-side rate limiting for Bodhan.

Bodhan counts retries against the request limit, so EVERY attempt (first try
and each retry) must take a slot here.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable


class RateLimiter:
    """Sliding-window requests-per-minute cap + max in-flight requests + 429 cooldown."""

    def __init__(
        self,
        rpm: int,
        concurrency: int,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rpm < 1 or concurrency < 1:
            raise ValueError("rpm and concurrency must be >= 1")
        self._rpm = rpm
        self._window = window_s
        self._clock = clock
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(concurrency)
        self._cooldown_until = 0.0

    def cool_down(self, seconds: float) -> None:
        """Pause all requests on this limiter (used when the server answers 429 + Retry-After)."""
        self._cooldown_until = max(self._cooldown_until, self._clock() + seconds)

    def headroom(self) -> int:
        """Requests that could start right now without waiting (0 during a 429 cooldown).

        Lets optional work (live captions) use only spare capacity, never delaying required work.
        """
        now = self._clock()
        if now < self._cooldown_until:
            return 0
        recent = sum(1 for t in self._stamps if now - t < self._window)
        return max(0, self._rpm - recent)

    async def _take_rpm_slot(self) -> None:
        async with self._lock:  # FIFO-ish: one waiter decides at a time
            while True:
                now = self._clock()
                if now < self._cooldown_until:
                    await asyncio.sleep(self._cooldown_until - now)
                    continue
                while self._stamps and now - self._stamps[0] >= self._window:
                    self._stamps.popleft()
                if len(self._stamps) < self._rpm:
                    self._stamps.append(now)
                    return
                await asyncio.sleep(self._window - (now - self._stamps[0]) + 0.01)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._sem:
            await self._take_rpm_slot()
            yield
