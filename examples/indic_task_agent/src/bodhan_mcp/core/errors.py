"""One structured error shape for every failure a tool can report."""
from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

F = TypeVar("F", bound=Callable[..., Any])


class BodhanError(Exception):
    """An expected, explainable failure. Never carries Indic text or secrets."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        hint: str | None = None,
        request_id: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.hint = hint
        self.request_id = request_id
        self.status = status  # upstream HTTP status, for callers that branch on it; not serialised

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "hint": self.hint,
                "request_id": self.request_id,
            }
        }

    def __str__(self) -> str:
        return json.dumps(self.payload())


def tool_guard(fn: F) -> F:
    """Turn any exception into an MCP ToolError whose text is the JSON error payload."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except BodhanError as exc:
            raise ToolError(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - the server must never crash the client
            err = BodhanError("INTERNAL", f"{type(exc).__name__}: {exc}", hint="Unexpected failure; the input was not processed.")
            raise ToolError(str(err)) from None

    return wrapper  # type: ignore[return-value]
