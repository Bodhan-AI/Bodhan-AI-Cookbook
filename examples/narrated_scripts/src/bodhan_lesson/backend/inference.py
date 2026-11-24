"""Shared Indic model routing, remote retry policy and attempt recording."""

import gc
import math
import re
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import BoundedSemaphore
from time import perf_counter, sleep
from typing import TypeVar

import httpx

from .config import BackendMode
from .models import Usage
from .report import AttemptLog

T = TypeVar("T")
MAX_REMOTE_ATTEMPTS = 3
RATE_LIMIT_BASE_DELAY = 5
LOCAL_USAGE = Usage(1, "request", None, 0, "measured", "Local inference")
CUDA_OOM = "Local CUDA out of memory"
# Shared by all hosted adapters and companion LLM clients in the process.
_HTTP_REQUEST_SLOT = BoundedSemaphore(1)


class ProviderError(Exception):
    """A sanitized failure safe to put in artifacts."""

    def __init__(self, category: str, reason: str, *, retryable: bool = False,
                 retry_after: float | None = None):
        super().__init__(reason)
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


def release_cuda_cache() -> None:
    """Clear released CUDA allocations without importing torch on hosted runs."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_initialized():
        torch.cuda.empty_cache()


def is_cuda_oom(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        kind = type(current)
        # Inspect the type without importing torch or initializing CUDA in the router.
        if any(
            base.__name__ == "OutOfMemoryError"
            and base.__module__ in {"torch", "torch.cuda", "torch._C"}
            for base in kind.__mro__
        ):
            return True
        if re.search(r"\b(?:cuda (?:error:\s*)?out of memory|cuda_error_out_of_memory)\b",
                     str(current), re.IGNORECASE):
            return True
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return False


def _retry_after(header: str | None) -> float | None:
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(header)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def post_response(
    client: httpx.Client, url: str, key: str, payload: dict, *,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    """Make one authenticated request. Persist neither headers nor server error bodies."""
    try:
        body = {"json": payload} if files is None else {"data": payload, "files": files}
        with _HTTP_REQUEST_SLOT:
            response = client.post(url, headers={"Authorization": f"Bearer {key}"}, **body)
    except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.LocalProtocolError):
        raise ProviderError("configuration", "Invalid endpoint or authentication configuration.") from None
    except httpx.TransportError as error:
        category = "timeout" if isinstance(error, httpx.TimeoutException) else "transport"
        raise ProviderError(category, f"Remote request {category} failure.", retryable=True) from None
    except httpx.RequestError:
        raise ProviderError("response", "Could not read the endpoint response.") from None

    status = response.status_code
    if not response.is_success:
        if status in {401, 403}:
            raise ProviderError("authentication", f"Endpoint refused credentials (HTTP {status}).")
        if status in {408, 429} or 500 <= status < 600:
            category = "rate_limit" if status == 429 else "transient_http"
            raise ProviderError(category, f"Endpoint returned HTTP {status}.", retryable=True,
                                retry_after=_retry_after(response.headers.get("Retry-After")))
        raise ProviderError("http", f"Endpoint returned HTTP {status}.")
    return response


def post_json(
    client: httpx.Client, url: str, key: str, payload: dict, *,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> dict:
    response = post_response(client, url, key, payload, files=files)
    try:
        result = response.json()
    except ValueError:
        raise ProviderError("response", "Endpoint returned invalid JSON.") from None
    if not isinstance(result, dict):
        raise ProviderError("response", "Endpoint returned an unexpected JSON value.")
    return result


def message_text(response: dict) -> str:
    try:
        text = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        text = None
    if not isinstance(text, str):
        raise ProviderError("response", "Chat response is missing its text content.")
    return text


class Inference:
    def __init__(self, attempts: AttemptLog, wait: Callable[[float], None] = sleep,
                 progress: Callable[[str], None] | None = None):
        self.attempts = attempts
        self.wait = wait
        self.progress = progress

    def run_indic(
        self, *, mode: BackendMode, model: str, entity_ids: tuple[str, ...],
        local: Callable[[], T], release_local: Callable[[], None],
        hosted: Callable[[], T], hosted_usage: Usage,
        measure_hosted_usage: Callable[[T], Usage] | None = None,
        local_model: str | None = None,
    ) -> T:
        if mode == "hosted":
            return self.run_remote(model, model, entity_ids, hosted, hosted_usage,
                                   measure_usage=measure_hosted_usage)

        local_model = local_model or model
        started = perf_counter()
        try:
            result = local()
        except Exception as error:
            if not is_cuda_oom(error):
                failure = error if isinstance(error, ProviderError) else ProviderError(
                    "local_failure", f"Local {model} failed ({type(error).__name__}). "
                    "Check the model installation, weights and CUDA runtime."
                )
                self._record(model, local_model, entity_ids, "local", 1, started, LOCAL_USAGE, failure)
                raise failure from None
        else:
            self._record(model, local_model, entity_ids, "local", 1, started, LOCAL_USAGE)
            return result

        failure = ProviderError("cuda_oom", CUDA_OOM)
        self._record(model, local_model, entity_ids, "local", 1, started, LOCAL_USAGE, failure)
        with suppress(Exception):
            release_local()
        return self.run_remote(model, model, entity_ids, hosted, hosted_usage, CUDA_OOM,
                               measure_usage=measure_hosted_usage)

    def run_remote(
        self, service: str, model: str, entity_ids: tuple[str, ...], call: Callable[[], T],
        usage: Usage, fallback_reason: str | None = None, *,
        measure_usage: Callable[[T], Usage] | None = None,
    ) -> T:
        for number in range(1, MAX_REMOTE_ATTEMPTS + 1):
            started = perf_counter()
            try:
                result = call()
            except ProviderError as error:
                attempt_usage = usage
                if error.category in {"configuration", "authentication", "duration_limit"}:
                    attempt_usage = Usage(0, usage.unit, usage.rate_inr, 0, "measured",
                                          "No billable use")
                self._record(service, model, entity_ids, "hosted", number, started,
                             attempt_usage, error, fallback_reason)
                if error.category == "rate_limit":
                    delay = error.retry_after if error.retry_after is not None else (
                        RATE_LIMIT_BASE_DELAY * 2 ** (number - 1)
                    )
                else:
                    delay = max(2 ** (number - 1), error.retry_after or 0)
                if not error.retryable:
                    raise
                if self.progress is not None and error.category == "rate_limit":
                    action = (f"Retrying in {delay:g}s." if number < MAX_REMOTE_ATTEMPTS
                              else f"Retries exhausted. Cooling down for {delay:g}s.")
                    self.progress(f"[{service}] Rate limit reached on attempt {number}. {action}")
                # Keep later pages from immediately repeating a rejected request. Even when
                # this request has exhausted its attempts, observe the provider's cooldown.
                if number == MAX_REMOTE_ATTEMPTS:
                    if error.category == "rate_limit":
                        self.wait(delay)
                    raise
                self.wait(delay)
            else:
                attempt_usage = measure_usage(result) if measure_usage else usage
                self._record(service, model, entity_ids, "hosted", number, started,
                             attempt_usage, fallback_reason=fallback_reason)
                return result
        raise AssertionError("Remote attempt loop must return or raise")

    def _record(
        self, service: str, model: str, entity_ids: tuple[str, ...], backend: str,
        number: int, started: float, usage: Usage, error: ProviderError | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        self.attempts.record(
            service=service, model=model, entity_ids=entity_ids, backend=backend, number=number,
            result="failed" if error else "ok", elapsed_ms=round((perf_counter() - started) * 1000),
            error_category=error.category if error else None, reason=str(error) if error else None,
            fallback_reason=fallback_reason, usage=usage,
        )
