
import asyncio

from httpx import (
    ConnectError,
    ConnectTimeout,
    ReadError,
    ReadTimeout,
    RemoteProtocolError,
)

from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)


class TransientMCPError(Exception):
    """Temporary MCP tool failure that may be retried."""


RETRYABLE_ERRORS = (
    ConnectError,
    ConnectTimeout,
    ReadError,
    ReadTimeout,
    RemoteProtocolError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
    TransientMCPError,
)


async def retry_async(
    operation,
    max_attempts=3,
    initial_delay=1.0,
    on_retry=None,
):
    """Retry an async operation on transient failures."""

    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        raise ValueError(
            "max_attempts must be a positive integer."
        )

    if initial_delay < 0:
        raise ValueError(
            "initial_delay cannot be negative."
        )

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()

        except RETRYABLE_ERRORS as exc:
            if attempt == max_attempts:
                raise

            delay = initial_delay * (2 ** (attempt - 1))

            if on_retry is not None:
                on_retry(
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    delay_seconds=delay,
                )

            await asyncio.sleep(delay)
