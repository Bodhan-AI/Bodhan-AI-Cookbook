"""Application errors and the single error envelope returned by the API.

Top-level imports are stdlib-only so the pure service layer can use these
classes without FastAPI installed. FastAPI is imported lazily in
``register_exception_handlers``.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("heritagelens.errors")


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details
        self.headers = headers


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_failed"
    message = "Authentication is required or the credentials are invalid."


class PermissionDenied(AppError):
    status_code = 403
    code = "permission_denied"
    message = "You do not have permission to perform this action."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"
    message = "The request could not be processed."


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "The uploaded file is too large."


class UnsupportedMediaType(AppError):
    status_code = 415
    code = "unsupported_media_type"
    message = "This file type is not supported."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "A required service is unavailable."


def error_body(code: str, message: str, request_id: str | None, details: Any = None) -> dict:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details is not None:
        body["error"]["details"] = details
    return body


def _clean_validation_errors(errors: list[dict]) -> list[dict]:
    """Drop echoed input values so submitted passwords/text never appear in responses."""
    return [
        {"loc": [str(p) for p in e.get("loc", [])], "message": e.get("msg", ""), "type": e.get("type", "")}
        for e in errors
    ]


def register_exception_handlers(app) -> None:  # pragma: no cover - needs fastapi
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.services.bodhan.exceptions import BodhanError, bodhan_to_app_error

    def rid(request: Request) -> str | None:
        return getattr(request.state, "request_id", None)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, rid(request), exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(BodhanError)
    async def _bodhan_error(request: Request, exc: BodhanError):
        mapped = bodhan_to_app_error(exc)
        logger.warning(
            "bodhan_error service=%s status=%s code=%s upstream_request_id=%s",
            exc.service, exc.status_code, exc.code, exc.request_id,
        )
        return JSONResponse(
            status_code=mapped.status_code,
            content=error_body(mapped.code, mapped.message, rid(request), mapped.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_body(
                "validation_failed", "Some fields are missing or invalid.", rid(request),
                _clean_validation_errors(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("http_error", str(exc.detail), rid(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("unhandled_exception request_id=%s", rid(request))
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An unexpected error occurred.", rid(request)),
        )
