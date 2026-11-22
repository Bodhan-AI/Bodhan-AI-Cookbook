"""Structured errors. Every tool failure carries a stable code, a message and a hint."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None,
                 retryable: bool = False, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": False, "code": self.code, "message": self.message,
                             "retryable": self.retryable}
        if self.hint:
            d["hint"] = self.hint
        if self.details:
            d["details"] = self.details
        return d


class BudgetExceeded(ToolFailure):
    def __init__(self, message: str):
        super().__init__("budget_exceeded", message,
                         hint="Stop calling paid tools. Finish with status 'partial' and report what was done.")
