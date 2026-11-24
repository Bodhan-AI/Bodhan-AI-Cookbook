"""Rupee cost tracking with a hard cap enforced before each paid call."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field

from .config import PRICE_SPEAK_PER_10K_CHARS, PRICE_TRANSCRIBE_PER_MINUTE, PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS


class BudgetExceeded(Exception):
    pass


def translate_cost(output_tokens: int) -> float:
    return output_tokens / 10_000 * PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS


def estimate_translate_tokens(text: str) -> int:
    # Indic output runs 1.5 to 2x the English token count; ~4 chars per English token.
    return max(16, int(len(text) / 4 * 2.2))


def speak_cost(chars: int) -> float:
    return chars / 10_000 * PRICE_SPEAK_PER_10K_CHARS


def transcribe_cost(seconds: float) -> float:
    return seconds / 60 * PRICE_TRANSCRIBE_PER_MINUTE


@dataclass
class CostTracker:
    max_inr: float | None = None
    spent: float = 0.0  # paid in this run (cache misses)
    reserved: float = 0.0
    attributed: dict[str, float] = field(default_factory=lambda: defaultdict(float))  # cold-cache cost of outputs used
    spent_by_model: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    calls: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    refused: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve(self, model: str, estimate: float) -> None:
        async with self._lock:
            if self.max_inr is not None and self.spent + self.reserved + estimate > self.max_inr:
                self.refused[model] += 1
                raise BudgetExceeded(f"{model}: estimated INR {estimate:.4f} would exceed --max-cost-inr {self.max_inr} (spent {self.spent:.4f})")
            self.reserved += estimate

    async def settle(self, model: str, estimate: float, actual: float) -> None:
        async with self._lock:
            self.reserved = max(self.reserved - estimate, 0.0)
            self.spent += actual
            self.spent_by_model[model] += actual
            self.calls[model] += 1

    async def release(self, estimate: float) -> None:
        async with self._lock:
            self.reserved = max(self.reserved - estimate, 0.0)

    def attribute(self, model: str, cost: float) -> None:
        self.attributed[model] += cost

    @property
    def exhausted(self) -> bool:
        return bool(self.refused)

    def snapshot(self) -> dict:
        return {
            "max_inr": self.max_inr,
            "spent_this_run_inr": round(self.spent, 4),
            "spent_by_model": {k: round(v, 4) for k, v in sorted(self.spent_by_model.items())},
            "paid_calls": dict(sorted(self.calls.items())),
            "refused_calls": dict(sorted(self.refused.items())),
        }
