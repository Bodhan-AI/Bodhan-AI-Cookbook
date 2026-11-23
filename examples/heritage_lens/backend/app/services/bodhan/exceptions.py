"""Bodhan exception types and the mapping to API errors (stdlib + app.core.errors only)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError


class BodhanError(Exception):
    """Failure talking to a Bodhan service (upstream error, timeout, bad response)."""

    def __init__(
        self,
        service: str,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.message = message
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.detail = detail

    def __str__(self) -> str:  # safe: never contains credentials
        bits = [f"[{self.service}]"]
        if self.status_code:
            bits.append(f"HTTP {self.status_code}")
        if self.code:
            bits.append(f"({self.code})")
        bits.append(self.message)
        return " ".join(bits)


class BodhanNotConfigured(BodhanError):
    pass


class BodhanTimeout(BodhanError):
    pass


class BodhanNetworkError(BodhanError):
    pass


class BodhanProtocolError(BodhanError):
    """HTTP 200 but the body is not what the documentation promises."""


class BodhanRequestInvalid(AppError):
    """Our own pre-flight validation rejected the request (nothing was sent)."""

    status_code = 400
    code = "invalid_request"


@dataclass
class BodhanResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    json: Any = None
    request_id: str | None = None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


def bodhan_to_app_error(exc: BodhanError) -> AppError:
    """Translate an upstream failure into a safe, user-facing API error."""
    svc = exc.service
    if isinstance(exc, BodhanNotConfigured):
        return AppError(
            f"The '{svc}' AI service is not configured on this server. "
            "An administrator must set the Bodhan API key in the backend environment.",
            status_code=503, code="ai_service_not_configured",
        )
    if isinstance(exc, BodhanTimeout):
        return AppError(f"The '{svc}' AI service took too long to respond. Please try again.",
                        status_code=504, code="ai_service_timeout")
    if isinstance(exc, BodhanNetworkError):
        return AppError(f"The '{svc}' AI service could not be reached. Please try again later.",
                        status_code=502, code="ai_service_unreachable")
    if isinstance(exc, BodhanProtocolError):
        return AppError(f"The '{svc}' AI service returned an unexpected response.",
                        status_code=502, code="ai_service_bad_response")
    status = exc.status_code or 502
    snippet = (exc.message or "")[:300]
    if status == 401:
        return AppError(
            f"The '{svc}' AI service rejected the server's credentials. Contact the administrator.",
            status_code=502, code="ai_service_auth_failed",
        )
    if status == 429:
        return AppError(f"The '{svc}' AI service is rate limiting requests. Please retry shortly.",
                        status_code=429, code="ai_service_rate_limited")
    if status == 413:
        return AppError(f"The input is too large for the '{svc}' AI service. {snippet}",
                        status_code=413, code="ai_service_payload_too_large")
    if status == 415:
        return AppError(f"The '{svc}' AI service does not accept this file format. {snippet}",
                        status_code=415, code="ai_service_unsupported_media")
    if status in (400, 422):
        return AppError(f"The '{svc}' AI service rejected the request: {snippet}",
                        status_code=status, code="ai_service_rejected_request",
                        details={"upstream_code": exc.code})
    return AppError(f"The '{svc}' AI service failed ({status}). Please try again later.",
                    status_code=502, code="ai_service_failed")
