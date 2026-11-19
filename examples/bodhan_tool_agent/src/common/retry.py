"""Resilient retry logic with exponential backoff, rate limit handling, and concurrency limits.

Handles transient HTTP errors (429, 500, 502, 503, 504) and connection/timeout exceptions,
while immediately failing non-retryable client errors (400, 401, 403, 404) with actionable
ToolErrors.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import os
import time
from typing import Any, Awaitable, Callable, TypeVar

import httpx

try:
    from mcp.server.fastmcp.exceptions import ToolError
except ImportError:
    class ToolError(Exception):  # type: ignore
        """Fallback ToolError exception when MCP package is unavailable."""
        pass

from src.common.logger import get_logger

logger = get_logger("retry")

T = TypeVar("T")

# Transient status codes that warrant retry with backoff
RETRYABLE_STATUS_CODES: set[int] = {429, 500, 502, 503, 504}

# Non-retryable status codes that should fail immediately with descriptive guidance
NON_RETRYABLE_STATUS_CODES: set[int] = {400, 401, 403, 404, 422}

# Default network/transport exceptions that warrant retry
RETRYABLE_NETWORK_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
)

# Optional OpenAI exception handling
try:
    import openai
    OPENAI_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
        openai.RateLimitError,
        openai.InternalServerError,
        openai.APIConnectionError,
        openai.APITimeoutError,
    )
    OPENAI_NON_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
        openai.AuthenticationError,
        openai.BadRequestError,
        openai.PermissionDeniedError,
        openai.NotFoundError,
    )
except ImportError:
    OPENAI_RETRYABLE_EXCEPTIONS = ()
    OPENAI_NON_RETRYABLE_EXCEPTIONS = ()


class _RetryableHTTPStatusError(Exception):
    """Internal exception raised when an HTTP response has a transient status code."""
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        super().__init__(f"Transient HTTP {response.status_code}")


# Concurrency limiter
_DEFAULT_CONCURRENCY = int(os.getenv("BODHAN_MAX_CONCURRENCY", "5"))
_GLOBAL_SEMAPHORE: asyncio.Semaphore | None = None
_GLOBAL_LOOP: asyncio.AbstractEventLoop | None = None


def get_concurrency_semaphore(limit: int = _DEFAULT_CONCURRENCY) -> asyncio.Semaphore:
    """Get or create the global API concurrency limiter semaphore.
    
    Guarantees the semaphore is always bound to the active running event loop,
    preventing cross-loop deadlock issues in pytest-asyncio and multi-threaded runtimes.
    """
    global _GLOBAL_SEMAPHORE, _GLOBAL_LOOP
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _GLOBAL_SEMAPHORE is None or _GLOBAL_LOOP is not current_loop:
        _GLOBAL_SEMAPHORE = asyncio.Semaphore(limit)
        _GLOBAL_LOOP = current_loop
    return _GLOBAL_SEMAPHORE


def reset_concurrency_semaphore(limit: int = _DEFAULT_CONCURRENCY) -> asyncio.Semaphore:
    """Reset the global concurrency semaphore (useful for testing with custom limits)."""
    global _GLOBAL_SEMAPHORE, _GLOBAL_LOOP
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    _GLOBAL_SEMAPHORE = asyncio.Semaphore(limit)
    _GLOBAL_LOOP = current_loop
    return _GLOBAL_SEMAPHORE


def parse_retry_after(response: httpx.Response, fallback_delay: float) -> float:
    """Parse Retry-After header from an HTTP response, falling back to computed delay."""
    retry_header = response.headers.get("Retry-After")
    if retry_header:
        try:
            val = float(retry_header.strip())
            if val >= 0:
                return val
        except ValueError:
            pass
    return fallback_delay


def format_non_retryable_error(response: httpx.Response) -> ToolError:
    """Convert a 4xx non-retryable response into an actionable ToolError with diagnostic guidance."""
    code = response.status_code
    body = response.text.strip()
    # Truncate very long bodies
    if len(body) > 300:
        body = body[:297] + "..."

    if code == 401:
        msg = (
            f"Authentication failed (HTTP 401 Unauthorized): {body}. "
            "Guidance: Verify that your Bodhan API key (BODHAN_*_KEY) is correctly "
            "configured and valid in your environment."
        )
    elif code == 403:
        msg = (
            f"Access forbidden (HTTP 403 Forbidden): {body}. "
            "Guidance: Your API key does not have permission for this resource or model. "
            "Check subscription tier or model access."
        )
    elif code == 404:
        msg = (
            f"Resource or endpoint not found (HTTP 404 Not Found): {body}. "
            f"Guidance: Verify the target URL/endpoint '{response.url}'."
        )
    elif code == 400:
        msg = (
            f"Invalid request parameters (HTTP 400 Bad Request): {body}. "
            "Guidance: The request payload was rejected. Check input file format, "
            "parameters, or supported options."
        )
    elif code == 422:
        msg = (
            f"Unprocessable entity (HTTP 422): {body}. "
            "Guidance: The payload syntax is valid but semantic parameters failed validation."
        )
    else:
        msg = f"HTTP client error ({code}): {body}."

    return ToolError(msg)


def format_exhausted_error(response: httpx.Response, max_retries: int) -> ToolError:
    """Format a ToolError when transient retries are exhausted."""
    code = response.status_code
    body = response.text.strip()
    if len(body) > 300:
        body = body[:297] + "..."

    if code == 429:
        return ToolError(
            f"Rate limit exceeded (HTTP 429) after {max_retries} retries: {body}. "
            "Guidance: Bodhan API rate limits reached. Reduce concurrency or request frequency."
        )
    return ToolError(
        f"Bodhan API service error (HTTP {code}) after {max_retries} retries: {body}. "
        "Guidance: Remote server is temporarily unavailable. Try again later."
    )


def compute_backoff_delay(
    attempt: int,
    initial_delay: float,
    backoff_factor: float,
    max_delay: float,
    response: httpx.Response | None = None,
) -> float:
    """Calculate exponential backoff delay with Retry-After header awareness."""
    nominal_delay = initial_delay * (backoff_factor ** attempt)
    if response is not None and response.status_code == 429:
        delay = parse_retry_after(response, nominal_delay)
    else:
        delay = nominal_delay
    return min(max(0.0, delay), max_delay)


async def retry_async(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    use_concurrency_limit: bool = True,
    semaphore: asyncio.Semaphore | None = None,
    **kwargs: Any,
) -> Any:
    """Execute an async callable with resilient exponential backoff retry.
    
    Automatically catches transient status codes (429, 500, 502, 503, 504),
    network connection/timeout exceptions, and respects Retry-After headers.
    Non-retryable 4xx errors fail immediately without useless retries.
    
    Args:
        func: Async callable or function returning an Awaitable / httpx.Response.
        *args: Positional arguments for func.
        max_retries: Maximum number of retry attempts (default 3).
        initial_delay: Initial sleep duration in seconds before first retry.
        backoff_factor: Multiplier for exponential backoff (default 2.0).
        max_delay: Upper cap on backoff sleep in seconds.
        use_concurrency_limit: Whether to acquire the concurrency semaphore.
        semaphore: Custom asyncio.Semaphore instance (optional).
        **kwargs: Keyword arguments for func.
    """
    sem = semaphore if semaphore is not None else (get_concurrency_semaphore() if use_concurrency_limit else None)

    for attempt in range(max_retries + 1):
        try:
            if sem is not None:
                async with sem:
                    raw_res = func(*args, **kwargs)
                    res = await raw_res if inspect.isawaitable(raw_res) else raw_res
            else:
                raw_res = func(*args, **kwargs)
                res = await raw_res if inspect.isawaitable(raw_res) else raw_res

            # If result is an httpx.Response, check its status code
            if isinstance(res, httpx.Response):
                if res.status_code in RETRYABLE_STATUS_CODES:
                    raise _RetryableHTTPStatusError(res)
                if res.status_code in NON_RETRYABLE_STATUS_CODES or res.status_code >= 400:
                    raise format_non_retryable_error(res)

            return res

        except _RetryableHTTPStatusError as err:
            resp = err.response
            if attempt >= max_retries:
                logger.error(
                    "HTTP %d error persisted after %d retries from %s: %s",
                    resp.status_code, max_retries, resp.url, resp.text
                )
                raise format_exhausted_error(resp, max_retries) from err

            delay = compute_backoff_delay(attempt, initial_delay, backoff_factor, max_delay, resp)
            logger.warning(
                "Transient HTTP %d from %s; retrying in %.2fs (attempt %d/%d)...",
                resp.status_code, resp.url, delay, attempt + 1, max_retries
            )
            await asyncio.sleep(delay)

        except RETRYABLE_NETWORK_EXCEPTIONS as err:
            if attempt >= max_retries:
                logger.error("Network/timeout error persisted after %d retries: %s", max_retries, err)
                raise ToolError(
                    f"Network or timeout error after {max_retries} retries ({type(err).__name__}: {str(err)}). "
                    "Guidance: Check network connectivity and API server availability."
                ) from err

            delay = compute_backoff_delay(attempt, initial_delay, backoff_factor, max_delay)
            logger.warning(
                "Transient network error (%s: %s); retrying in %.2fs (attempt %d/%d)...",
                type(err).__name__, str(err), delay, attempt + 1, max_retries
            )
            await asyncio.sleep(delay)

        except ToolError:
            # Non-retryable ToolError: propagate immediately
            raise

        except Exception as err:
            # Handle OpenAI transient exceptions if available
            if OPENAI_RETRYABLE_EXCEPTIONS and isinstance(err, OPENAI_RETRYABLE_EXCEPTIONS):
                if attempt >= max_retries:
                    logger.error("LLM API transient error persisted after %d retries: %s", max_retries, err)
                    raise ToolError(f"LLM API error after {max_retries} retries: {str(err)}") from err
                delay = compute_backoff_delay(attempt, initial_delay, backoff_factor, max_delay)
                logger.warning(
                    "Transient LLM API error (%s); retrying in %.2fs (attempt %d/%d)...",
                    err, delay, attempt + 1, max_retries
                )
                await asyncio.sleep(delay)
            elif OPENAI_NON_RETRYABLE_EXCEPTIONS and isinstance(err, OPENAI_NON_RETRYABLE_EXCEPTIONS):
                logger.error("Non-retryable LLM API error: %s", err)
                raise ToolError(f"LLM API client error ({type(err).__name__}): {str(err)}") from err
            else:
                # Other unexpected exceptions: propagate or wrap
                logger.error("Unexpected exception in %s: %s", getattr(func, "__name__", str(func)), err)
                raise


def retry_sync(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> T:
    """Execute a synchronous callable with resilient exponential backoff retry.
    
    Handles transient network/API exceptions and status codes.
    """
    for attempt in range(max_retries + 1):
        try:
            res = func(*args, **kwargs)

            if isinstance(res, httpx.Response):
                if res.status_code in RETRYABLE_STATUS_CODES:
                    raise _RetryableHTTPStatusError(res)
                if res.status_code in NON_RETRYABLE_STATUS_CODES or res.status_code >= 400:
                    raise format_non_retryable_error(res)

            return res

        except _RetryableHTTPStatusError as err:
            resp = err.response
            if attempt >= max_retries:
                logger.error("HTTP %d error persisted after %d retries: %s", resp.status_code, max_retries, resp.text)
                raise format_exhausted_error(resp, max_retries) from err

            delay = compute_backoff_delay(attempt, initial_delay, backoff_factor, max_delay, resp)
            logger.warning("Transient HTTP %d; retrying in %.2fs (attempt %d/%d)...", resp.status_code, delay, attempt + 1, max_retries)
            time.sleep(delay)

        except RETRYABLE_NETWORK_EXCEPTIONS as err:
            if attempt >= max_retries:
                logger.error("Network error persisted after %d retries: %s", max_retries, err)
                raise ToolError(
                    f"Network or timeout error after {max_retries} retries ({type(err).__name__}: {str(err)})."
                ) from err

            delay = compute_backoff_delay(attempt, initial_delay, backoff_factor, max_delay)
            logger.warning("Transient network error (%s); retrying in %.2fs (attempt %d/%d)...", err, delay, attempt + 1, max_retries)
            time.sleep(delay)

        except ToolError:
            raise

        except Exception as err:
            if OPENAI_RETRYABLE_EXCEPTIONS and isinstance(err, OPENAI_RETRYABLE_EXCEPTIONS):
                if attempt >= max_retries:
                    logger.error("LLM API transient error persisted after %d retries: %s", max_retries, err)
                    raise ToolError(f"LLM API error after {max_retries} retries: {str(err)}") from err
                delay = compute_backoff_delay(attempt, initial_delay, backoff_factor, max_delay)
                logger.warning("Transient LLM API error (%s); retrying in %.2fs...", err, delay)
                time.sleep(delay)
            elif OPENAI_NON_RETRYABLE_EXCEPTIONS and isinstance(err, OPENAI_NON_RETRYABLE_EXCEPTIONS):
                logger.error("Non-retryable LLM API error: %s", err)
                raise ToolError(f"LLM API client error ({type(err).__name__}): {str(err)}") from err
            else:
                logger.error("Unexpected exception in %s: %s", getattr(func, "__name__", str(func)), err)
                raise


def with_retry(
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    use_concurrency_limit: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to apply resilient retries to any async or sync function."""
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await retry_async(
                    fn,
                    *args,
                    max_retries=max_retries,
                    initial_delay=initial_delay,
                    backoff_factor=backoff_factor,
                    max_delay=max_delay,
                    use_concurrency_limit=use_concurrency_limit,
                    **kwargs,
                )
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                return retry_sync(
                    fn,
                    *args,
                    max_retries=max_retries,
                    initial_delay=initial_delay,
                    backoff_factor=backoff_factor,
                    max_delay=max_delay,
                    **kwargs,
                )
            return sync_wrapper
    return decorator
