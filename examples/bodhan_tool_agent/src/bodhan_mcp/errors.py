"""Structured error definitions for Bodhan MCP and agent components."""

from __future__ import annotations

from typing import Any


class BodhanError(Exception):
    """Base exception for all Bodhan operations."""

    def __init__(self, message: str, code: str = "BODHAN_ERROR", details: dict[str, Any] | None = None, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "retryable": self.retryable,
            }
        }


class BodhanAPIError(BodhanError):
    """Raised when the Bodhan API returns an HTTP error status."""

    def __init__(self, status_code: int, message: str, code: str = "API_ERROR", request_id: str | None = None):
        retryable = status_code in {429, 500, 502, 503, 504}
        details = {"status_code": status_code, "request_id": request_id}
        super().__init__(message=message, code=code, details=details, retryable=retryable)
        self.status_code = status_code
        self.request_id = request_id


class BodhanRateLimitError(BodhanAPIError):
    """Raised when HTTP 429 rate limit or quota is encountered."""

    def __init__(self, message: str, retry_after: float | None = None, request_id: str | None = None):
        super().__init__(
            status_code=429,
            message=message,
            code="RATE_LIMIT_EXCEEDED",
            request_id=request_id,
        )
        self.retry_after = retry_after
        if retry_after:
            self.details["retry_after"] = retry_after


class BodhanAuthenticationError(BodhanAPIError):
    """Raised when API credentials are missing or invalid (HTTP 401/403)."""

    def __init__(self, message: str, request_id: str | None = None):
        super().__init__(
            status_code=401,
            message=message,
            code="AUTHENTICATION_FAILED",
            request_id=request_id,
        )
        self.retryable = False


class UnsupportedLanguageError(BodhanError):
    """Raised when an unsupported language or language direction is requested."""

    def __init__(self, language: str, capability: str, supported: list[str]):
        msg = (
            f"Language '{language}' is not supported for {capability}. "
            f"Supported languages: {', '.join(sorted(supported))}."
        )
        super().__init__(
            message=msg,
            code="UNSUPPORTED_LANGUAGE",
            details={"requested_language": language, "capability": capability, "supported": supported},
            retryable=False,
        )


class UnsupportedVoiceError(BodhanError):
    """Raised when an invalid voice is requested for a given language."""

    def __init__(self, voice: str, language: str, available_voices: list[str]):
        msg = (
            f"Voice '{voice}' is not available for language '{language}'. "
            f"Available voices: {', '.join(available_voices)}."
        )
        super().__init__(
            message=msg,
            code="UNSUPPORTED_VOICE",
            details={"voice": voice, "language": language, "available": available_voices},
            retryable=False,
        )


class InvalidInputError(BodhanError):
    """Raised when input files or arguments fail validation."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(
            message=message,
            code="INVALID_INPUT",
            details={"field": field} if field else {},
            retryable=False,
        )


class FileAccessError(BodhanError):
    """Raised when a file path violates security policy or cannot be accessed."""

    def __init__(self, message: str, path: str):
        super().__init__(
            message=message,
            code="FILE_ACCESS_ERROR",
            details={"path": path},
            retryable=False,
        )


class BudgetExceededError(BodhanError):
    """Raised when an operation would exceed the configured budget or step count."""

    def __init__(self, message: str, current_val: float, max_val: float, unit: str = "INR"):
        super().__init__(
            message=message,
            code="BUDGET_EXCEEDED",
            details={"current": current_val, "limit": max_val, "unit": unit},
            retryable=False,
        )


class PromptInjectionError(BodhanError):
    """Raised when untrusted content contains malicious agent instructions."""

    def __init__(self, message: str, snippet: str):
        super().__init__(
            message=message,
            code="PROMPT_INJECTION_DETECTED",
            details={"snippet": snippet},
            retryable=False,
        )
