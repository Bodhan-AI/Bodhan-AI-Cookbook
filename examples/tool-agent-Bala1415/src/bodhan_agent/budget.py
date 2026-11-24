"""
Budget controller — enforces step count, cost (₹), and per-call timeout limits.

Provides clean shutdown when any budget is exhausted.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BudgetController:
    """
    Tracks and enforces execution budgets.

    Raises BudgetExhausted when any limit is reached.
    """

    max_steps: int = 30
    max_cost_inr: float = 20.0
    timeout_seconds: float = 300.0  # Overall task timeout

    # Internal state
    current_step: int = 0
    total_cost_inr: float = 0.0
    start_time: float = field(default_factory=time.time)
    step_log: list[dict[str, Any]] = field(default_factory=list)

    def check(self) -> None:
        """Check all budgets. Raises BudgetExhausted if any is exceeded."""
        if self.current_step >= self.max_steps:
            raise BudgetExhausted(
                f"Step limit reached: {self.current_step}/{self.max_steps}",
                budget_type="steps",
            )
        if self.total_cost_inr > self.max_cost_inr:
            raise BudgetExhausted(
                f"Cost budget exceeded: ₹{self.total_cost_inr:.2f}/₹{self.max_cost_inr:.2f}",
                budget_type="cost",
            )
        elapsed = time.time() - self.start_time
        if elapsed > self.timeout_seconds:
            raise BudgetExhausted(
                f"Timeout exceeded: {elapsed:.0f}s/{self.timeout_seconds:.0f}s",
                budget_type="timeout",
            )

    def record_step(
        self,
        tool: str,
        cost_inr: float = 0.0,
        latency_seconds: float = 0.0,
        details: str = "",
    ) -> None:
        """Record a completed step and update counters."""
        self.current_step += 1
        self.total_cost_inr += cost_inr
        self.step_log.append({
            "step": self.current_step,
            "tool": tool,
            "cost_inr": round(cost_inr, 4),
            "cumulative_cost_inr": round(self.total_cost_inr, 4),
            "latency_seconds": round(latency_seconds, 2),
            "elapsed_seconds": round(time.time() - self.start_time, 2),
            "details": details,
        })
        logger.info(
            f"Step {self.current_step}/{self.max_steps} | "
            f"₹{self.total_cost_inr:.2f}/₹{self.max_cost_inr:.2f} | "
            f"{tool}: {details}"
        )

    def can_afford(self, estimated_cost_inr: float) -> bool:
        """Check if a planned call fits within the remaining budget."""
        return (self.total_cost_inr + estimated_cost_inr) <= self.max_cost_inr

    def remaining(self) -> dict[str, Any]:
        """Return remaining budget info."""
        elapsed = time.time() - self.start_time
        return {
            "steps_remaining": self.max_steps - self.current_step,
            "cost_remaining_inr": round(self.max_cost_inr - self.total_cost_inr, 4),
            "time_remaining_seconds": round(self.timeout_seconds - elapsed, 1),
        }

    def summary(self) -> dict[str, Any]:
        """Return a full budget summary for report.json."""
        elapsed = time.time() - self.start_time
        return {
            "total_steps": self.current_step,
            "max_steps": self.max_steps,
            "total_cost_inr": round(self.total_cost_inr, 4),
            "max_cost_inr": self.max_cost_inr,
            "elapsed_seconds": round(elapsed, 2),
            "timeout_seconds": self.timeout_seconds,
            "step_log": self.step_log,
        }


class BudgetExhausted(Exception):
    """Raised when a budget limit is reached."""

    def __init__(self, message: str, budget_type: str = "unknown"):
        self.budget_type = budget_type
        super().__init__(message)
