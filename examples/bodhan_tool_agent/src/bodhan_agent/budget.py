"""
Budget enforcement: tracks step count and INR cost per run.

Call can_proceed() before every tool call. Call record_step() after.
When the budget is exhausted the agent stops cleanly via BudgetExceeded.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when a step would exceed the configured budget."""


@dataclass
class BudgetManager:
    max_steps: int
    max_cost_inr: float
    _steps: int = field(default=0, init=False, repr=False)
    _cost: float = field(default=0.0, init=False, repr=False)

    @property
    def steps_used(self) -> int:
        return self._steps

    @property
    def cost_used(self) -> float:
        return self._cost

    def can_proceed(self, estimated_cost: float = 0.0) -> bool:
        return (
            self._steps < self.max_steps
            and (self._cost + estimated_cost) <= self.max_cost_inr
        )

    def assert_can_proceed(self, estimated_cost: float = 0.0) -> None:
        if not self.can_proceed(estimated_cost):
            raise BudgetExceeded(
                f"Budget exhausted: {self._steps}/{self.max_steps} steps, "
                f"₹{self._cost:.4f}/₹{self.max_cost_inr} spent."
            )

    def record_step(self, actual_cost: float) -> None:
        self._steps += 1
        self._cost += actual_cost

    def summary(self) -> dict:
        return {
            "steps_used": self._steps,
            "steps_limit": self.max_steps,
            "cost_used_inr": round(self._cost, 4),
            "cost_limit_inr": self.max_cost_inr,
        }
