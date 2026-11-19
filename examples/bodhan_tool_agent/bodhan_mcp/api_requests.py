"""Shared admission and retry policy for the four hosted Bodhan models."""

from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
from math import isfinite
from threading import BoundedSemaphore
from time import sleep
from typing import Any

import httpx
from mcp.server.fastmcp.exceptions import ToolError

from .request_budget import BudgetExhausted, RequestBudget, active_budget

MAX_CONCURRENT_REQUESTS = 4
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 2
SLOT_TIMEOUT_SECONDS = 5
_request_slots = BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
_PRE_SEND_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class _SlotsBusy(ToolError):
    pass


_RELEASE_FINGERPRINT_ERRORS = (*_PRE_SEND_ERRORS, _SlotsBusy, BudgetExhausted)


def _hash_binary(value: bytes) -> dict[str, str]:
    # Multipart audio uses bytes. Keep the canonical representation small.
    return {"sha256": sha256(value).hexdigest()}


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    backoff = 0.25 * 2 ** attempt
    if retry_after is None:
        return backoff
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(retry_after)
            delay = (deadline - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return backoff
    return max(backoff, delay) if isfinite(delay) else backoff


def post_bodhan(
    client: httpx.Client, url: str, model: str, cost: Decimal, **kwargs: Any
) -> tuple[httpx.Response, int | None]:
    """Use a per-run cache and retry definite rejections and pre-send failures."""
    budget = active_budget.get()
    if budget is None:
        return _post_with_retries(client, url, model, cost, budget, **kwargs)

    payload = json.dumps([url, model, kwargs], sort_keys=True, default=_hash_binary)
    fingerprint = sha256(payload.encode()).hexdigest()
    cached = budget.start_request(fingerprint)
    if cached is not None:
        return cached, None

    try:
        response, reservation = _post_with_retries(client, url, model, cost, budget, **kwargs)
    except _RELEASE_FINGERPRINT_ERRORS:
        budget.forget_request(fingerprint)
        raise
    if response.status_code == 200:
        budget.cache_response(fingerprint, response)
    elif response.status_code == 429:
        budget.forget_request(fingerprint)
    return response, reservation


def _post_with_retries(
    client: httpx.Client, url: str, model: str, cost: Decimal, budget: RequestBudget | None, **kwargs: Any
) -> tuple[httpx.Response, int | None]:
    for attempt in range(MAX_ATTEMPTS):
        if not _request_slots.acquire(timeout=SLOT_TIMEOUT_SECONDS):
            raise _SlotsBusy("concurrency_limit: Bodhan request slots are busy. Try again shortly.")
        try:
            reservation = budget.reserve(model, cost) if budget is not None else None
            try:
                response = client.post(url, **kwargs)
            except _PRE_SEND_ERRORS:
                if reservation is not None:
                    budget.settle(reservation, Decimal(0))
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                delay = _retry_delay(attempt, None)
            else:
                if response.status_code != 429:
                    return response, reservation
                if reservation is not None:
                    budget.settle(reservation, Decimal(0))
                delay = _retry_delay(attempt, response.headers.get("Retry-After"))
                if attempt == MAX_ATTEMPTS - 1 or delay > MAX_RETRY_DELAY_SECONDS:
                    return response, None
        finally:
            _request_slots.release()
        sleep(delay)
    raise AssertionError("Bodhan request loop must return a response or raise")
