"""Prices (in rupees) and a running cost tracker with a hard budget."""

from __future__ import annotations

import threading
from typing import Any

from bodhan_mcp.errors import BodhanError

# Published prices.
TRANSCRIBE_PER_MIN = 0.10
TRANSLATE_PER_10K_OUT_TOKENS = 0.20
SPEAK_PER_10K_CHARS = 6.00
OCR_PER_IMAGE = 0.20


def cost_transcribe(seconds: float) -> float:
    return seconds / 60 * TRANSCRIBE_PER_MIN


def cost_translate(output_tokens: int) -> float:
    return output_tokens / 10_000 * TRANSLATE_PER_10K_OUT_TOKENS


def cost_speak(chars: int) -> float:
    return chars / 10_000 * SPEAK_PER_10K_CHARS


class BudgetExceeded(BodhanError):
    """A call was refused because it could push spending past the budget."""

    def __init__(self, message: str) -> None:
        super().__init__(
            "budget_exceeded",
            message,
            retryable=False,
            hint="Stop, and report what was completed and what was left undone.",
        )


class CostTracker:
    """Adds up spending per model. `check` is called BEFORE a paid call."""

    def __init__(self, max_cost_inr: float | None = None) -> None:
        self.max_cost_inr = max_cost_inr
        self.total = 0.0
        self.by_model: dict[str, float] = {}
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def check(self, model: str, estimate_inr: float) -> None:
        with self._lock:
            if self.max_cost_inr is not None and self.total + estimate_inr > self.max_cost_inr + 1e-9:
                raise BudgetExceeded(
                    f"{model} call (~Rs {estimate_inr:.3f}) would exceed the budget of "
                    f"Rs {self.max_cost_inr:.2f}; Rs {self.total:.3f} already spent."
                )

    def add(self, model: str, inr: float, **detail: Any) -> None:
        with self._lock:
            self.total += inr
            self.by_model[model] = self.by_model.get(model, 0.0) + inr
            self.calls.append({"model": model, "inr": round(inr, 5), **detail})

    @property
    def remaining(self) -> float | None:
        return None if self.max_cost_inr is None else max(0.0, self.max_cost_inr - self.total)