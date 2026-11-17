"""Async retry helper with exponential backoff and full jitter."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

T = TypeVar("T")


class RetryError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, message: str, last_status: int | None, attempts: int) -> None:
        super().__init__(message)
        self.last_status = last_status
        self.attempts = attempts


async def with_retry(
    coro_fn: Callable[[], Awaitable[T]],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_codes: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> T:
    """Execute *coro_fn* with exponential backoff and full jitter on transient errors.

    Args:
        coro_fn: A zero-argument async callable that returns the result or
            raises :class:`httpx.HTTPStatusError`.
        max_attempts: Maximum number of total attempts (including the first).
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper cap on the computed delay (before jitter).
        retryable_codes: HTTP status codes that trigger a retry.  4xx codes
            other than 429 are *never* retried.

    Returns:
        The return value of *coro_fn* on success.

    Raises:
        RetryError: If all attempts are exhausted.
        httpx.HTTPStatusError: For non-retryable HTTP errors (re-raised immediately).
    """
    last_exc: httpx.HTTPStatusError | None = None
    last_status: int | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_fn()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code

            # Non-retryable 4xx (not 429)
            if 400 <= status < 500 and status != 429:
                raise

            if status not in retryable_codes:
                raise

            last_exc = exc
            last_status = status

            if attempt == max_attempts:
                break

            # Determine delay
            if status == 429:
                retry_after_raw = exc.response.headers.get("Retry-After")
                if retry_after_raw is not None:
                    try:
                        delay = float(retry_after_raw)
                    except ValueError:
                        delay = _jitter_delay(attempt, base_delay, max_delay)
                else:
                    delay = _jitter_delay(attempt, base_delay, max_delay)
            else:
                delay = _jitter_delay(attempt, base_delay, max_delay)

            await asyncio.sleep(delay)

    raise RetryError(
        f"All {max_attempts} attempts failed; last status {last_status}",
        last_status=last_status,
        attempts=max_attempts,
    )


def _jitter_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Full-jitter exponential backoff: uniform(0, min(max_delay, base * 2^attempt))."""
    cap = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return random.uniform(0.0, cap)
