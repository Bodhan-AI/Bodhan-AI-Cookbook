"""Run budgets: steps, rupees and wall-clock time. Enforced in code, not left to the LLM."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Budget:
    max_steps: int = 30
    max_cost_inr: float = 20.0
    max_runtime_s: float = 900.0
    steps: int = 0
    bodhan_cost_inr: float = 0.0
    llm_cost_inr: float = 0.0
    started: float = field(default_factory=time.monotonic)

    @property
    def cost_inr(self) -> float:
        return self.bodhan_cost_inr + self.llm_cost_inr

    @property
    def remaining_cost(self) -> float:
        return max(0.0, self.max_cost_inr - self.cost_inr)

    @property
    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps)

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def exhausted(self) -> str | None:
        """Why the run must stop now, or None."""
        if self.steps >= self.max_steps:
            return f"step budget used up ({self.steps}/{self.max_steps} steps)"
        if self.cost_inr >= self.max_cost_inr - 1e-9 and self.max_cost_inr >= 0:
            return f"cost budget used up (Rs {self.cost_inr:.3f} of Rs {self.max_cost_inr:.2f})"
        if self.elapsed_s >= self.max_runtime_s:
            return f"time budget used up ({self.elapsed_s:.0f}s of {self.max_runtime_s:.0f}s)"
        return None

    def status_line(self) -> str:
        return (f"Budget: {self.remaining_steps} of {self.max_steps} steps left; Rs {self.remaining_cost:.3f} of "
                f"Rs {self.max_cost_inr:.2f} left; {max(0, self.max_runtime_s - self.elapsed_s):.0f}s of time left.")
