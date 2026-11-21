"""Typed result envelope + the `guarded` decorator that turns every failure into a structured error."""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, Field

from bodhan_core.errors import ToolError

log = logging.getLogger("bodhan_mcp")


class ErrorInfo(BaseModel):
    code: str = Field(description="Machine-readable code, e.g. file_not_found, unsupported_language, budget_exceeded, rate_limited.")
    message: str = Field(description="What went wrong.")
    hint: str = Field("", description="What to do next.")
    retryable: bool = Field(False, description="True if the same call may succeed later.")
    details: dict[str, Any] | None = None


class ToolResult(BaseModel):
    """Every tool returns a subclass of this. Check `ok` first; on failure read `error`."""

    ok: bool = Field(True, description="False when the tool failed; see `error`.")
    error: ErrorInfo | None = Field(None, description="Present only when ok is false.")
    cost_inr: float = Field(0.0, description="Rupees spent on Bodhan model calls by this tool call (0 for cache hits).")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues worth reporting to the user.")


class Artifact(BaseModel):
    path: str = Field(description="Path of the produced file (relative to the work directory's artifacts/ parent).")
    kind: str = Field(description="One of: text, markdown, srt, audio, json.")
    ai_generated: bool = Field(False, description="True for synthesized speech; must be labelled as AI-generated to users.")


R = TypeVar("R", bound=ToolResult)


def guarded(result_cls: type[R]) -> Callable[[Callable[..., R]], Callable[..., R]]:
    """Never let an exception reach the MCP client: convert it to `{ok: false, error: {...}}`."""

    def deco(fn: Callable[..., R]) -> Callable[..., R]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> R:
            try:
                return fn(*args, **kwargs)
            except ToolError as e:
                return result_cls(ok=False, error=ErrorInfo(code=e.code, message=e.message, hint=e.hint, retryable=e.retryable, details=e.details or None))
            except Exception as e:  # noqa: BLE001 - last line of defence for a long-running server
                log.exception("tool %s crashed", fn.__name__)
                return result_cls(ok=False, error=ErrorInfo(code="internal_error", message=f"{type(e).__name__}: {e}", hint="Report this; try different input."))

        return wrapper

    return deco


def preview(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more chars in the saved file]"
