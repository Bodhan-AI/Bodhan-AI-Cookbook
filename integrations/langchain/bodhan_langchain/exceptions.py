"""Exceptions for Bodhan API client and integrations."""

from __future__ import annotations

from typing import Any


class BodhanError(Exception):
    """Base exception for all Bodhan client errors."""


class BodhanValidationError(BodhanError, ValueError):
    """Raised when client-side parameter validation fails before sending a request."""

    def __init__(self, message: str, parameter: str | None = None) -> None:
        super().__init__(message)
        self.parameter = parameter


class BodhanAPIError(BodhanError):
    """Raised when the Bodhan API returns an error response or a network failure occurs."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        error_code: str | None = None,
        request_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.request_id = request_id
        self.response_body = response_body

    def __str__(self) -> str:
        details: list[str] = [self.message]
        if self.status_code is not None:
            details.append(f"status={self.status_code}")
        if self.error_code:
            details.append(f"code={self.error_code}")
        if self.request_id:
            details.append(f"request_id={self.request_id}")
        return " | ".join(details)


class BodhanAuthenticationError(BodhanAPIError):
    """Raised when authentication fails (HTTP 401 or missing API key)."""

    def __init__(
        self,
        message: str = "Invalid or missing Bodhan API key",
        status_code: int | None = 401,
        error_code: str | None = "AUTHENTICATION_ERROR",
        request_id: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status_code,
            error_code=error_code,
            request_id=request_id,
            response_body=response_body,
        )


class BodhanRateLimitError(BodhanAPIError):
    """Raised when API rate limit is exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str = "Bodhan API rate limit exceeded",
        status_code: int = 429,
        error_code: str | None = "RATE_LIMIT_EXCEEDED",
        request_id: str | None = None,
        retry_after: float | None = None,
        limit_requests: str | None = None,
        limit_parallel_requests: str | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(
            message=message,
            status_code=status_code,
            error_code=error_code,
            request_id=request_id,
            response_body=response_body,
        )
        self.retry_after = retry_after
        self.limit_requests = limit_requests
        self.limit_parallel_requests = limit_parallel_requests

    def __str__(self) -> str:
        base = super().__str__()
        extras: list[str] = []
        if self.retry_after is not None:
            extras.append(f"retry_after={self.retry_after}s")
        if self.limit_requests:
            extras.append(f"limit_requests={self.limit_requests}")
        if self.limit_parallel_requests:
            extras.append(f"limit_parallel={self.limit_parallel_requests}")
        return f"{base} ({', '.join(extras)})" if extras else base
