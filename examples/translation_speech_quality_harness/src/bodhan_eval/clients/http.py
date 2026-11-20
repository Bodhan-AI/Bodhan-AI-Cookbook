"""HTTP retry with exponential backoff + jitter, honouring Retry-After on 429/503."""

from __future__ import annotations

import asyncio
import logging
import random
import time

import httpx

log = logging.getLogger("bodhan_eval.http")

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class APIError(RuntimeError):
    def __init__(
        self,
        service: str,
        status: int | None,
        code: str | None,
        message: str,
        request_id: str | None = None,
    ):
        super().__init__(f"{service}: HTTP {status} {code or ''} {message}".strip())
        self.service = service
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in RETRY_STATUS


class CacheMiss(RuntimeError):
    """Raised in offline mode when an output is not in the cache."""


def _parse_error(service: str, resp: httpx.Response) -> APIError:
    code = msg = rid = None
    try:
        err = resp.json().get("error", {})
        if isinstance(err, dict):
            code, msg, rid = err.get("code"), err.get("message"), err.get("request_id")
    except ValueError:  # non-JSON error body; fall back to status + text
        code = None
    rid = rid or resp.headers.get("x-request-id")
    return APIError(service, resp.status_code, code, msg or resp.text[:200], rid)


def _retry_after(resp: httpx.Response | None) -> float | None:
    if resp is None:
        return None
    v = resp.headers.get("retry-after")
    try:
        return min(60.0, float(v)) if v else None
    except ValueError:
        return None


# Indirection so tests can avoid real sleeping.
sleep = asyncio.sleep
monotonic = time.monotonic


class RateGate:
    """Shared pacing for one service.

    * `block(seconds)`: after a 429, every caller waits until the server's Retry-After has
      passed, instead of each retrying on its own and hitting the limit again.
    * `rpm`: optional steady limit (requests per minute); calls are spaced 60/rpm apart.
    * Adaptive pacing: every 429 doubles the spacing between calls (up to `max_interval`),
      every success shrinks it by 5% back towards the floor. Bodhan's 429s carry no
      Retry-After, so without this concurrent callers keep hitting the limit together.
    """

    def __init__(self, rpm: float | None = None, max_interval: float = 10.0):
        self.floor = 60.0 / rpm if rpm else 0.0
        self.interval = self.floor
        self.max_interval = max_interval
        self._next = 0.0
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    def block(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, monotonic() + seconds)

    def penalize(self) -> None:
        self.interval = min(self.max_interval, max(0.5, self.interval * 2))

    def reward(self) -> None:
        self.interval = max(self.floor, self.interval * 0.95)

    async def wait(self) -> None:
        async with self._lock:
            now = monotonic()
            start = max(now, self._next, self._blocked_until)
            self._next = start + self.interval
        if start > now:
            await sleep(start - now)


async def request_with_retry(
    client: httpx.AsyncClient,
    service: str,
    method: str,
    url: str,
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    gate: RateGate | None = None,
    **kwargs,
) -> httpx.Response:
    last: APIError | None = None
    for attempt in range(1, max_attempts + 1):
        resp = None
        if gate is not None:
            await gate.wait()
        t0 = time.monotonic()
        try:
            resp = await client.request(method, url, **kwargs)
            log.info(
                "api_call",
                extra={
                    "fields": {
                        "service": service,
                        "status": resp.status_code,
                        "attempt": attempt,
                        "seconds": round(time.monotonic() - t0, 2),
                    }
                },
            )
            if resp.status_code < 400:
                if gate is not None:
                    gate.reward()
                return resp
            last = _parse_error(service, resp)
            if not last.retryable:
                raise last
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = APIError(service, None, type(e).__name__, str(e) or type(e).__name__)
        if attempt == max_attempts:
            break
        delay = _retry_after(resp) or base_delay * 2 ** (attempt - 1) * (0.5 + random.random())
        if gate is not None and resp is not None and resp.status_code == 429:
            gate.penalize()  # slow every caller down, not just this one
            gate.block(delay)  # everyone waits; this caller retries through the gate
            delay = 0.0
        log.warning(
            "retry",
            extra={
                "fields": {
                    "service": service,
                    "attempt": attempt,
                    "delay_s": round(delay, 2),
                    "error": str(last),
                }
            },
        )
        if delay:
            await sleep(delay)
    assert last is not None
    raise last
