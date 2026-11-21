"""Runtime step and INR budget tracker with pre-call reservation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


class BudgetExceededError(RuntimeError):
    """Raised before a paid call or step if it would exceed --max-cost-inr or --max-steps."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # "cost" | "steps"


@dataclass
class BudgetTracker:
    max_steps: int
    max_cost_inr: float
    steps_used: int = 0
    spent_inr: float = 0.0
    reserved_inr: float = 0.0
    cost_by_model: dict[str, float] = field(default_factory=dict)
    cache_hits: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def remaining_inr(self) -> float:
        return max(0.0, round(self.max_cost_inr - self.spent_inr - self.reserved_inr, 6))

    @property
    def remaining_steps(self) -> int:
        return max(0, self.max_steps - self.steps_used)

    async def consume_step(self, label: str = "") -> int:
        async with self._lock:
            if self.steps_used + 1 > self.max_steps:
                raise BudgetExceededError(
                    "steps",
                    f"Step budget exhausted ({self.steps_used}/{self.max_steps}) before '{label}'.",
                )
            self.steps_used += 1
            return self.steps_used

    async def reserve_cost(self, model: str, estimated_inr: float, detail: str = "") -> float:
        """Reserve estimated cost before a paid call. Rejects cleanly if it would exceed max_cost_inr."""
        async with self._lock:
            est = max(0.0, float(estimated_inr))
            if self.spent_inr + self.reserved_inr + est > self.max_cost_inr + 1e-9:
                raise BudgetExceededError(
                    "cost",
                    f"Budget cap ₹{self.max_cost_inr:.2f} would be exceeded by {model} "
                    f"(spent ₹{self.spent_inr:.4f} + reserved ₹{self.reserved_inr:.4f} + est ₹{est:.4f}; {detail}).",
                )
            self.reserved_inr = round(self.reserved_inr + est, 6)
            return est

    async def settle_cost(
        self,
        model: str | None,
        actual_inr: float,
        *,
        reserved_amount: float = 0.0,
        cache_hit: bool = False,
    ) -> None:
        async with self._lock:
            self.reserved_inr = max(0.0, round(self.reserved_inr - reserved_amount, 6))
            if cache_hit:
                self.cache_hits += 1
            if actual_inr > 0:
                self.spent_inr = round(self.spent_inr + actual_inr, 6)
                key = model or "unknown"
                self.cost_by_model[key] = round(self.cost_by_model.get(key, 0.0) + actual_inr, 6)
