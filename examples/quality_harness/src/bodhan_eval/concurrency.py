"""Async concurrency + retry layer: a global semaphore bounds in-flight API calls, and
retries use exponential backoff with jitter on 429/5xx/timeout, honoring Retry-After.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Raised by clients to signal a transient failure worth retrying."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    rng: random.Random | None = None,
) -> T:
    rng = rng or random
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except RetryableError as exc:
            if attempt >= max_attempts:
                raise
            if exc.retry_after is not None:
                delay = exc.retry_after
            else:
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay += rng.uniform(0, delay * 0.25)  # jitter
            await asyncio.sleep(delay)


class ConcurrencyLimiter:
    def __init__(self, limit: int):
        self._sem = asyncio.Semaphore(max(1, limit))

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._sem:
            return await fn()
