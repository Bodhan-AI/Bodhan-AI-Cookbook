"""Run budgets: steps, rupees and wall-clock time.

Cost is enforced on the server side too: every paid tool call carries ``max_cost_inr`` equal to
this run's remaining budget (split evenly across calls issued in parallel), and the tool refuses
before sending any request whose estimate exceeds it. So the run can never overspend, even if
the LLM issues many calls at once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

PAID_TOOLS = {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"}


@dataclass
class Budget:
    max_steps: int = 30
    max_cost_inr: float = 20.0
    max_time_s: float = 900.0
    call_timeout_s: float = 300.0
    steps: int = 0
    spent_inr: float = 0.0
    started: float = field(default_factory=time.monotonic)
    cost_by_tool: dict[str, float] = field(default_factory=dict)
    cost_by_model: dict[str, float] = field(default_factory=dict)

    @property
    def remaining_inr(self) -> float:
        return max(0.0, round(self.max_cost_inr - self.spent_inr, 6))

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.max_time_s - self.elapsed_s)

    @property
    def steps_left(self) -> int:
        return max(0, self.max_steps - self.steps)

    def share_for(self, n_parallel_paid: int) -> float:
        return round(self.remaining_inr / max(1, n_parallel_paid), 6)

    def charge(self, tool: str, cost: float, calls: list[dict] | None = None) -> None:
        cost = float(cost or 0.0)
        self.spent_inr = round(self.spent_inr + cost, 6)
        self.cost_by_tool[tool] = round(self.cost_by_tool.get(tool, 0.0) + cost, 6)
        for c in calls or []:
            m = c.get("model", "?")
            self.cost_by_model[m] = round(self.cost_by_model.get(m, 0.0) + float(c.get("cost_inr") or 0.0), 6)

    def call_timeout(self) -> float:
        return max(1.0, min(self.call_timeout_s, self.remaining_s))

    def snapshot(self) -> dict:
        return {
            "max_steps": self.max_steps, "steps_used": self.steps,
            "max_cost_inr": self.max_cost_inr, "spent_inr": round(self.spent_inr, 4),
            "remaining_inr": round(self.remaining_inr, 4),
            "max_time_s": self.max_time_s, "elapsed_s": round(self.elapsed_s, 2),
            "call_timeout_s": self.call_timeout_s,
        }
