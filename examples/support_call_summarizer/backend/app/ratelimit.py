"""A small async requests-per-minute limiter (sliding 60 s window)."""

from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    def __init__(self, per_minute: int, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.per_minute = per_minute
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep

    async def acquire(self) -> None:
        if self.per_minute <= 0:
            return
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= 60:
                    self._sent.popleft()
                if len(self._sent) < self.per_minute:
                    self._sent.append(now)
                    return
                await self._sleep(60 - (now - self._sent[0]) + 0.01)
