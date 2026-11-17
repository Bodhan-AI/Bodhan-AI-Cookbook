"""HTTP with bounded retries: exponential backoff plus jitter on 429/5xx and transport errors."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from .. import log

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
QUOTA_HINTS = ("budget", "quota", "credit", "insufficient", "balance", "exhausted")


class ServiceError(Exception):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retryable = retryable


class ServiceUnavailable(ServiceError):
    """Missing key, bad key, or exhausted quota: stop calling this service for the rest of the run."""


def _error_detail(resp: httpx.Response) -> tuple[str, str | None]:
    try:
        body = resp.json()
        err = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(err, dict):
            return str(err.get("message") or err)[:400], err.get("code")
        return str(err)[:400], None
    except ValueError:
        return resp.text[:400], None


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return min(float(value), 30.0)
    except ValueError:
        return None


async def request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    service: str,
    attempts: int = 5,
    timeout: float = 90.0,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            resp = await client.request(method, url, timeout=timeout, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = ServiceError(f"{service}: {type(exc).__name__}: {exc}", retryable=True)
            log.event("http_retry", service=service, attempt=attempt, error=str(last))
        else:
            latency_ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code < 400:
                log.event("http_ok", service=service, status=resp.status_code, latency_ms=latency_ms, attempt=attempt)
                return resp
            message, code = _error_detail(resp)
            lowered = f"{message} {code or ''}".lower()
            if resp.status_code in (401, 403):
                raise ServiceUnavailable(f"{service}: {resp.status_code} {code}: {message}", status=resp.status_code, code=code)
            # Some providers describe a temporary concurrency limit as "based on
            # remaining balance". That is still a retryable 429, not a dead key.
            concurrency_limit = "concurren" in lowered
            if resp.status_code in (402,) or (resp.status_code == 429 and not concurrency_limit and any(h in lowered for h in QUOTA_HINTS)):
                raise ServiceUnavailable(f"{service}: quota or budget exhausted: {message}", status=resp.status_code, code=code)
            if resp.status_code not in RETRYABLE_STATUS:
                raise ServiceError(f"{service}: {resp.status_code} {code}: {message}", status=resp.status_code, code=code)
            last = ServiceError(f"{service}: {resp.status_code} {code}: {message}", status=resp.status_code, code=code, retryable=True)
            log.event("http_retry", service=service, attempt=attempt, status=resp.status_code, latency_ms=latency_ms)
            wait = _retry_after(resp)
            if wait is not None and attempt < attempts:
                await asyncio.sleep(wait + random.uniform(0, 0.5))
                continue
        if attempt < attempts:
            await asyncio.sleep(min(base_delay * 2 ** (attempt - 1), 20.0) * random.uniform(0.5, 1.5))
    assert last is not None
    raise last
