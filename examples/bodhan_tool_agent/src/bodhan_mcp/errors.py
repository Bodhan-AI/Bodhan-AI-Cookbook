from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class BudgetExceeded(ToolError):
    def __init__(self, message: str = "The next Bodhan request would exceed the cost budget.") -> None:
        super().__init__("budget_exceeded", message, False)
