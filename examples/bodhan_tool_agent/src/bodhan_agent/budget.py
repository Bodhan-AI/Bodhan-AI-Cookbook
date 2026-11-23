"""Budget manager: tracking step count, INR costs, timeouts, and early stopping."""

from __future__ import annotations

import logging
import time
from bodhan_mcp.errors import BudgetExceededError

logger = logging.getLogger("bodhan_agent.budget")


class BudgetManager:
    """Monitors and enforces execution step limits, monetary budgets in INR, and timeouts."""

    def __init__(
        self,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        per_call_timeout_s: float = 60.0,
        max_retries_per_step: int = 3,
    ):
        self.max_steps = max_steps
        self.max_cost_inr = max_cost_inr
        self.per_call_timeout_s = per_call_timeout_s
        self.max_retries_per_step = max_retries_per_step

        self.current_steps: int = 0
        self.current_cost_inr: float = 0.0
        self.start_time: float = time.time()

    @property
    def remaining_budget_inr(self) -> float:
        return max(0.0, self.max_cost_inr - self.current_cost_inr)

    @property
    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.current_steps)

    def check_can_execute_step(self, estimated_step_cost_inr: float = 0.0):
        """Verify whether an upcoming step can be executed within limits."""
        if self.current_steps >= self.max_steps:
            raise BudgetExceededError(
                f"Maximum allowed steps ({self.max_steps}) reached.",
                current_val=self.current_steps,
                max_val=self.max_steps,
                unit="steps",
            )

        if (self.current_cost_inr + estimated_step_cost_inr) > (self.max_cost_inr + 0.001):
            raise BudgetExceededError(
                f"Step estimated cost (₹{estimated_step_cost_inr:.2f}) would exceed remaining budget (₹{self.remaining_budget_inr:.2f}). Total limit: ₹{self.max_cost_inr:.2f}.",
                current_val=round(self.current_cost_inr + estimated_step_cost_inr, 2),
                max_val=self.max_cost_inr,
                unit="INR",
            )

    def record_step_execution(self, actual_cost_inr: float):
        """Record the completion of a step and accumulate cost."""
        self.current_steps += 1
        self.current_cost_inr = round(self.current_cost_inr + actual_cost_inr, 4)
        logger.info(
            f"Step {self.current_steps}/{self.max_steps} executed. "
            f"Cost: +₹{actual_cost_inr:.4f} | Total: ₹{self.current_cost_inr:.4f}/₹{self.max_cost_inr:.2f}"
        )

        if self.current_cost_inr > self.max_cost_inr:
            logger.warning(f"Cost threshold reached: ₹{self.current_cost_inr:.2f} > ₹{self.max_cost_inr:.2f}")

    def to_dict(self) -> dict:
        return {
            "current_steps": self.current_steps,
            "max_steps": self.max_steps,
            "current_cost_inr": self.current_cost_inr,
            "max_cost_inr": self.max_cost_inr,
            "remaining_budget_inr": self.remaining_budget_inr,
            "elapsed_seconds": round(time.time() - self.start_time, 2),
        }
