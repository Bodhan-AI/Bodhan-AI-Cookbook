from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from time import monotonic


class RateLimiter:
    def __init__(
        self,
        max_requests: int,
        period_s: float = 60.0,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if period_s <= 0:
            raise ValueError("period_s must be > 0")
        self.max_requests = max_requests
        self.period_s = period_s
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _drop_expired(self, now: float) -> None:
        while self._sent and now - self._sent[0] >= self.period_s:
            self._sent.popleft()

    async def acquire(self) -> float:
        waited = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                self._drop_expired(now)
                if len(self._sent) < self.max_requests:
                    self._sent.append(now)
                    return waited
                wait = self.period_s - (now - self._sent[0])
                await self._sleep(wait)
                waited += wait
