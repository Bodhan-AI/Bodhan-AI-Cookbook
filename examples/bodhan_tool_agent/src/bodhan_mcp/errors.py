"""Structured errors. Tools never raise raw exceptions to the caller; they return ToolError payloads."""
from __future__ import annotations


class ToolError(Exception):
    """An expected, actionable failure. `code` is stable and machine-readable; `hint` tells the caller what to do."""

    def __init__(self, code: str, message: str, hint: str = "", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "retryable": self.retryable,
            },
        }


class BudgetExceeded(ToolError):
    def __init__(self, spent: float, cap: float, needed: float = 0.0):
        super().__init__(
            "budget_exceeded",
            f"Cost cap Rs {cap:.2f} would be exceeded (spent Rs {spent:.2f}, next call ~Rs {needed:.2f}).",
            "Stop and report what was completed; do not retry.",
        )
