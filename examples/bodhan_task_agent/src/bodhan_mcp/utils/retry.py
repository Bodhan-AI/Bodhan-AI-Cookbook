from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import TypeVar

from bodhan_mcp.exceptions import BodhanToolError

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_RETRY_AFTER_S = 30.0


def backoff_delay(attempt: int, base_s: float = 1.0, cap_s: float = 16.0) -> float:
    return min(base_s * 2**attempt, cap_s) + random.uniform(0, base_s / 2)


def retry_wait(error: BodhanToolError, attempt: int) -> float:
    retry_after = error.details.get("retry_after_s")
    if (
        isinstance(retry_after, (int, float))
        and not isinstance(retry_after, bool)
        and math.isfinite(retry_after)
        and retry_after >= 0
    ):
        return min(float(retry_after), MAX_RETRY_AFTER_S)
    return backoff_delay(attempt)


def _deadline_error(label: str, max_elapsed_s: float, attempts: int) -> BodhanToolError:
    return BodhanToolError(
        "timeout",
        f"{label} did not finish within its {max_elapsed_s:.0f}s deadline.",
        hint="Retry with a larger deadline or a smaller input.",
        retryable=True,
        details={"attempts": attempts, "deadline_s": max_elapsed_s},
    )


async def with_retries(
    call: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    label: str,
    max_elapsed_s: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if max_elapsed_s is not None and (not math.isfinite(max_elapsed_s) or max_elapsed_s <= 0):
        raise ValueError("max_elapsed_s must be finite and > 0")

    start = monotonic()
    attempt = 0

    while True:
        remaining = None if max_elapsed_s is None else max_elapsed_s - (monotonic() - start)
        if remaining is not None and remaining <= 0:
            raise _deadline_error(label, max_elapsed_s, attempt)

        try:
            if remaining is None:
                return await call()
            return await asyncio.wait_for(call(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise _deadline_error(label, max_elapsed_s or 0.0, attempt + 1) from exc
        except BodhanToolError as error:
            attempts = attempt + 1
            if not error.retryable:
                raise
            if attempt >= max_retries:
                error.details["attempts"] = attempts
                logger.warning("call=%s gave_up attempts=%d error=%s:%s", label, attempts, error.status, error.code)
                raise

            wait = retry_wait(error, attempt)
            if max_elapsed_s is not None and wait >= max_elapsed_s - (monotonic() - start):
                error.details["attempts"] = attempts
                error.details["deadline_s"] = max_elapsed_s
                logger.warning("call=%s out_of_time attempts=%d error=%s:%s", label, attempts, error.status, error.code)
                raise

            logger.warning(
                "call=%s attempt=%d error=%s:%s retrying_in_s=%.2f", label, attempts, error.status, error.code, wait
            )
            await sleep(wait)
            attempt += 1
