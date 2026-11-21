"""Exceptions for Bodhan API client and integrations."""

from __future__ import annotations

import re
from typing import Any


def sanitize_sensitive_text(text: str, max_chars: int = 200) -> str:
    """Sanitize sensitive credentials, authorization tokens, and base64 payloads from error messages."""
    if not text:
        return ""
    # Redact Bodhan/OpenAI-style keys
    sanitized = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "sk-***[REDACTED]", text)
    # Redact Bearer tokens
    sanitized = re.sub(r"(Bearer\s+)[A-Za-z0-9_.-]+", r"\1***[REDACTED]", sanitized, flags=re.IGNORECASE)
    # Redact base64 data URLs
    sanitized = re.sub(r"data:image/[a-zA-Z0-9.+_-]+;base64,[A-Za-z0-9+/=]+", "data:image/...;base64,***[REDACTED]", sanitized)
    # Redact long bare base64 chunks
    sanitized = re.sub(r"[A-Za-z0-9+/=]{80,}", "***[REDACTED_PAYLOAD]***", sanitized)
    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars] + "..."
    return sanitized


class BodhanError(Exception):
    """Base exception for all Bodhan client errors."""


class BodhanValidationError(BodhanError, ValueError):
    """Raised when client-side parameter validation fails before sending a request."""

    def __init__(self, message: str, parameter: str | None = None) -> None:
        clean_msg = sanitize_sensitive_text(message)
        super().__init__(clean_msg)
        self.message = clean_msg
        self.parameter = parameter

    def __repr__(self) -> str:
        param_str = f", parameter='{self.parameter}'" if self.parameter else ""
        return f"<{self.__class__.__name__}: '{self.message}'{param_str}>"


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
        clean_msg = sanitize_sensitive_text(message)
        super().__init__(clean_msg)
        self.message = clean_msg
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

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}: {self.__str__()}>"


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
