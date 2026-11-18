
"""Retry temporary Gemini planner API failures."""

import random
import time

from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
)


def create_plan_with_retry(
    create,
    *,
    max_attempts=3,
    initial_delay=1.0,
    max_delay=8.0,
    sleep_fn=time.sleep,
    on_retry=None,
    **kwargs,
):
    """Retry connection errors, HTTP 429, and HTTP 5xx."""

    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts < 1
    ):
        raise ValueError(
            "max_attempts must be a positive integer"
        )

    for attempt in range(1, max_attempts + 1):
        try:
            return create(**kwargs)

        except (
            APIConnectionError,
            APITimeoutError,
            APIStatusError,
        ) as exc:

            status = getattr(exc, "status_code", None)

            retryable = (
                isinstance(
                    exc,
                    (APIConnectionError, APITimeoutError),
                )
                or status == 429
                or (
                    status is not None
                    and 500 <= status <= 599
                )
            )

            if not retryable or attempt == max_attempts:
                raise

            delay = min(
                max_delay,
                initial_delay * (2 ** (attempt - 1)),
            )

            delay += random.uniform(
                0,
                delay * 0.1,
            )

            if on_retry is not None:
                on_retry(
                    attempt,
                    type(exc).__name__,
                    round(delay, 2),
                )

            sleep_fn(delay)
