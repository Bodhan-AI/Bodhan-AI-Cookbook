"""Budget and Resource Enforcement for Bodhan Task Agent.

Guarantees strict compliance with:
- Maximum execution step budget
- Maximum monetary cost budget in Indian Rupees (INR)
- Per-run and per-call timeout limits
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from bodhan_agent.models import BudgetState


class BudgetError(Exception):
    """Base exception for budget enforcement."""
    pass


class StepBudgetExhaustedError(BudgetError):
    """Raised when the step count limit is reached."""
    pass


class RupeeBudgetExceededError(BudgetError):
    """Raised when the monetary INR cost budget is reached or would be exceeded."""
    pass


class TimeoutBudgetExceededError(BudgetError):
    """Raised when elapsed time exceeds the allocated timeout."""
    pass


class BudgetTracker:
    """Monitors and enforces step count, INR cost, and execution duration."""

    def __init__(
        self,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
        timeout_seconds: float = 300.0,
    ):
        self.max_steps = max(1, max_steps)
        self.max_cost_inr = max(0.0, float(max_cost_inr))
        self.timeout_seconds = max(0.001, float(timeout_seconds))

        self.steps_taken = 0
        self.current_cost_inr = 0.0
        self.cost_breakdown: Dict[str, float] = {}
        self.start_time = time.time()

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed runtime in seconds."""
        return round(time.time() - self.start_time, 2)

    def check_timeout(self) -> None:
        """Check if overall task timeout has been exceeded."""
        if self.elapsed_seconds > self.timeout_seconds:
            raise TimeoutBudgetExceededError(
                f"Execution timed out: elapsed {self.elapsed_seconds:.1f}s exceeds "
                f"budget of {self.timeout_seconds:.1f}s."
            )

    def can_proceed(self) -> bool:
        """Non-raising check if agent can execute another step."""
        if self.steps_taken >= self.max_steps:
            return False
        if self.current_cost_inr >= self.max_cost_inr:
            return False
        if self.elapsed_seconds >= self.timeout_seconds:
            return False
        return True

    def record_step_start(self) -> int:
        """Record the start of an execution step, enforcing step limits."""
        self.check_timeout()
        if self.steps_taken >= self.max_steps:
            raise StepBudgetExhaustedError(
                f"Step limit reached: attempted step {self.steps_taken + 1} exceeds "
                f"maximum step budget of {self.max_steps}."
            )
        self.steps_taken += 1
        return self.steps_taken

    def check_cost_safety(self, estimated_additional_cost: float = 0.0) -> None:
        """Verify that an upcoming action will not exceed the INR budget."""
        self.check_timeout()
        potential_total = self.current_cost_inr + estimated_additional_cost
        if potential_total > self.max_cost_inr:
            raise RupeeBudgetExceededError(
                f"Monetary budget exceeded: potential cost ₹{potential_total:.4f} "
                f"exceeds maximum allowed budget of ₹{self.max_cost_inr:.2f}."
            )

    def record_cost(self, tool_name: str, cost_inr: float) -> None:
        """Record actual incurred cost for a tool execution."""
        clean_tool = tool_name.replace("bodhan_", "")
        self.current_cost_inr = round(self.current_cost_inr + cost_inr, 6)
        self.cost_breakdown[clean_tool] = round(
            self.cost_breakdown.get(clean_tool, 0.0) + cost_inr, 6
        )

        if self.current_cost_inr > self.max_cost_inr:
            raise RupeeBudgetExceededError(
                f"Monetary budget exceeded: current cost ₹{self.current_cost_inr:.4f} "
                f"exceeds maximum allowed budget of ₹{self.max_cost_inr:.2f}."
            )

    def get_state(self) -> BudgetState:
        """Export current budget state as a Pydantic model."""
        return BudgetState(
            max_steps=self.max_steps,
            steps_taken=self.steps_taken,
            max_cost_inr=self.max_cost_inr,
            current_cost_inr=self.current_cost_inr,
            timeout_seconds=self.timeout_seconds,
            elapsed_seconds=self.elapsed_seconds,
            cost_breakdown=dict(self.cost_breakdown),
        )
