"""Retry with exponential back-off and jitter using tenacity."""
from __future__ import annotations

import logging
import random
from functools import wraps
from typing import Any, Callable, Tuple, Type

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
    before_sleep_log,
    RetryCallState,
)

from bodhan_mcp.config import settings
from bodhan_mcp.core.errors import BodhanError, RATE_LIMITED, TIMEOUT, UPSTREAM_ERROR

logger = logging.getLogger(__name__)

# Codes that are safe to retry
_RETRYABLE_CODES = {RATE_LIMITED, TIMEOUT, UPSTREAM_ERROR}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, BodhanError):
        return exc.retryable
    return False


def bodhan_retry(func: Callable) -> Callable:
    """Decorator: retry up to max_retry_attempts on retryable BodhanErrors."""
    decorated = retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(settings.max_retry_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=30) + wait_random(0, 2),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(func)

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await decorated(*args, **kwargs)

    return wrapper
