"""
Resilience layer: retry with exponential backoff & jitter, Retry-After header parsing,
per-service concurrency semaphores, and circuit breakers.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Callable, Coroutine, Optional

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)


class CircuitBreakerOpen(Exception):
    """Raised when the circuit breaker is tripped due to consecutive 5xx errors."""

    pass


class CircuitBreaker:
    """Tracks consecutive 5xx server errors per service/language and trips when threshold exceeded."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.consecutive_failures: int = 0
        self.last_failure_time: float = 0.0
        self.state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = "CLOSED"

    def trip(self) -> None:
        """Forces the circuit breaker to OPEN state immediately."""
        self.state = "OPEN"
        self.consecutive_failures = self.failure_threshold
        self.last_failure_time = time.time()

    def record_failure(self, status_code: int) -> None:
        if status_code >= 500:
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            if self.consecutive_failures >= self.failure_threshold:
                self.state = "OPEN"

    def check_state(self) -> None:
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
            else:
                raise CircuitBreakerOpen(
                    f"Circuit breaker is OPEN after {self.consecutive_failures} consecutive 5xx errors. "
                    "Failing fast to isolate service degradation."
                )


class CircuitBreakerRegistry:
    """Manages isolated circuit breakers per (service, language) tuple."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}

    def get_breaker(self, service: str, lang: str = "default") -> CircuitBreaker:
        key = (service.lower(), lang.lower())
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(
                failure_threshold=self.failure_threshold,
                recovery_timeout=self.recovery_timeout,
            )
        return self._breakers[key]


def parse_retry_after(retry_after_str: Optional[str], default_backoff: float = 1.0) -> float:
    """B18: Parses Retry-After header as either seconds or HTTP-date."""
    if not retry_after_str:
        return default_backoff
    s = retry_after_str.strip()
    try:
        return max(0.1, float(s))
    except ValueError:
        pass
    try:
        import email.utils
        dt = email.utils.parsedate_to_datetime(s)
        diff = dt.timestamp() - time.time()
        return max(0.1, diff)
    except Exception:
        return default_backoff


def should_retry_exception(exc: BaseException) -> bool:
    """Retry on network timeouts, connection errors, and 429/5xx status codes."""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429 or code >= 500:
            return True
    return False


async def execute_with_resilience(
    coro_fn: Callable[[], Coroutine[Any, Any, httpx.Response]],
    semaphore: asyncio.Semaphore,
    circuit_breaker: Optional[CircuitBreaker] = None,
    max_attempts: int = 4,
    min_wait: float = 1.0,
    max_wait: float = 10.0,
) -> httpx.Response:
    """
    Executes an async HTTP request within a concurrency semaphore, guarded by
    circuit breaker and retried with backoff and Retry-After awareness.
    Releases semaphore while sleeping on 429/5xx to prevent slot starvation.
    """
    if circuit_breaker:
        circuit_breaker.check_state()

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential_jitter(initial=min_wait, max=max_wait, jitter=1.0),
        retry=retry_if_exception(should_retry_exception),
        reraise=True,
    ):
        with attempt:
            try:
                if circuit_breaker:
                    circuit_breaker.check_state()

                # C2: Acquire semaphore ONLY during network execution, releasing it before sleep
                async with semaphore:
                    resp = await coro_fn()

                # Check for 429 / 5xx to trigger retry
                if resp.status_code == 429:
                    # C1: Do not sleep after final attempt (only between attempts)
                    if attempt.retry_state.attempt_number < max_attempts:
                        retry_after_str = resp.headers.get("Retry-After")
                        base_backoff = 1.5**attempt.retry_state.attempt_number
                        if retry_after_str:
                            parsed_wait = parse_retry_after(retry_after_str, default_backoff=base_backoff)
                            wait_sec = max(parsed_wait, base_backoff)
                        else:
                            wait_sec = base_backoff
                        wait_sec += random.uniform(0.1, 0.5)
                        # Semaphore is released before sleeping!
                        await asyncio.sleep(wait_sec)
                    if resp._request is None:
                        resp._request = httpx.Request("POST", "https://api.bodhan.ai")
                    resp.raise_for_status()

                elif resp.status_code >= 500:
                    # C1: Do not record failure here; HTTPStatusError catch below records once per attempt
                    if resp._request is None:
                        resp._request = httpx.Request("POST", "https://api.bodhan.ai")
                    resp.raise_for_status()

                # Success (2xx or 4xx other than 429)
                if circuit_breaker and resp.status_code < 400:
                    circuit_breaker.record_success()
                return resp

            except (httpx.TimeoutException, httpx.ConnectTimeout):
                # B18: Count network timeouts toward circuit breaker failure count (504 Gateway Timeout)
                if circuit_breaker:
                    circuit_breaker.record_failure(504)
                raise
            except httpx.HTTPStatusError as e:
                if circuit_breaker and e.response.status_code >= 500:
                    circuit_breaker.record_failure(e.response.status_code)
                raise

    raise RuntimeError("Retry loop exited without response or exception")
