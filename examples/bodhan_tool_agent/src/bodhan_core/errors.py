"""Structured, actionable errors shared by every tool."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """An expected failure. Carries a machine-readable code and a hint on how to recover."""

    def __init__(self, code: str, message: str, hint: str = "", retryable: bool = False, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message, "hint": self.hint, "retryable": self.retryable}
        if self.details:
            d["details"] = self.details
        return d


class BudgetExceeded(ToolError):
    def __init__(self, message: str, **details: Any):
        super().__init__(
            "budget_exceeded",
            message,
            hint="Stop making paid calls. Finish with status 'partial' and report what was completed.",
            retryable=False,
            **details,
        )
