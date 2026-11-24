"""Step and rupee budgets for one run.

A step is one LLM turn (one chat completion). Tool calls issued in that turn,
including several run in parallel, belong to the same step. The rupee budget
covers Bodhan model calls only; the server's cost meter enforces it before
every paid request, and this object mirrors what the tool results report so
the planner can see how much is left.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    max_steps: int
    max_cost_inr: float
    steps_used: int = 0
    spent_inr: float = 0.0
    cost_exhausted: bool = False

    @property
    def steps_left(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    @property
    def remaining_inr(self) -> float:
        return max(0.0, self.max_cost_inr - self.spent_inr)

    @property
    def steps_exhausted(self) -> bool:
        return self.steps_used >= self.max_steps

    @property
    def exhausted(self) -> str | None:
        if self.cost_exhausted:
            return "cost"
        if self.steps_exhausted:
            return "steps"
        return None

    def use_step(self) -> None:
        self.steps_used += 1

    def charge(self, cost_inr: float | int | None) -> None:
        if cost_inr:
            self.spent_inr = round(self.spent_inr + float(cost_inr), 6)
        # Small residue still counts as exhausted: no paid call is that cheap.
        if self.remaining_inr < 1e-4:
            self.cost_exhausted = True

    def mark_cost_exhausted(self) -> None:
        self.cost_exhausted = True

    def snapshot(self) -> dict[str, float | int | bool]:
        return {
            "steps_used": self.steps_used,
            "steps_left": self.steps_left,
            "spent_inr": round(self.spent_inr, 4),
            "remaining_inr": round(self.remaining_inr, 4),
            "max_cost_inr": self.max_cost_inr,
        }
