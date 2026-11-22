"""HTTP helper: POST with retries and exponential backoff."""

from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)

RETRYABLE = {429, 500, 502, 503, 504}
# A 429 can also mean "account out of credit" (Bodhan: {"type": "budget_exceeded"}). That never clears by
# waiting, so it is not retried, and later calls with the same key fail at once without a request.
BUDGET_MARKERS = ("budget_exceeded", "exceededbudget", "over budget", "insufficient_quota", "insufficient credit")
_exhausted: dict[str, dict] = {}   # api key -> {"url", "message"}; the key itself is never logged or reported


class BudgetExceeded(RuntimeError):
    """The account behind this API key has no credit left."""


def is_budget_error(resp: requests.Response) -> bool:
    if resp.status_code not in (402, 429):
        return False
    text = (resp.text or "").lower()
    return any(m in text for m in BUDGET_MARKERS)


def exhausted_services() -> list[dict]:
    """Endpoints whose account ran out of credit during this run (for the report)."""
    return list(_exhausted.values())


def post_with_retry(
    url: str,
    key: str,
    *,
    max_attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    timeout: float = 180.0,
    **kwargs,
) -> requests.Response:
    """POST with Bearer auth. Retries 429/5xx and network errors with backoff.

    Returns the last response if all attempts fail with a retryable status;
    raises the last network error if no response was ever received.
    """
    if key in _exhausted:
        raise BudgetExceeded(_exhausted[key]["message"])
    last_exc: Exception | None = None
    resp: requests.Response | None = None
    for attempt in range(max_attempts):
        try:
            resp = requests.post(url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout, **kwargs)
            last_exc = None
            if is_budget_error(resp):
                msg = f"account out of credit at {url} (HTTP {resp.status_code}): {resp.text[:200]}"
                _exhausted[key] = {"url": url, "message": msg}
                log.error("api: %s", msg)
                raise BudgetExceeded(msg)
            if resp.status_code not in RETRYABLE:
                return resp
            reason = f"HTTP {resp.status_code}"
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc, resp, reason = e, None, type(e).__name__

        if attempt == max_attempts - 1:
            break
        retry_after = resp.headers.get("Retry-After") if resp is not None else None
        try:
            delay = float(retry_after) if retry_after else base_delay * 2**attempt
        except ValueError:
            delay = base_delay * 2**attempt
        delay = min(delay, max_delay) + random.uniform(0, 1)
        log.warning("api retry %d/%d in %.1fs (%s)", attempt + 1, max_attempts - 1, delay, reason)
        time.sleep(delay)

    if resp is not None:
        return resp
    raise last_exc  # type: ignore[misc]
