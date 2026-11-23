"""Estimated cost ledger in INR. Reservations are made before metered calls; actual provider
bills can differ because of retries, tokenization, pricing, or unreported usage. Cached calls cost 0.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    pass


@dataclass
class CostLedger:
    max_inr: float | None = None
    spent_by_model: dict[str, float] = field(default_factory=dict)
    estimated_flag: dict[str, bool] = field(default_factory=dict)
    reserved: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total(self) -> float:
        return sum(self.spent_by_model.values())

    def remaining(self) -> float | None:
        if self.max_inr is None:
            return None
        return self.max_inr - self.total - self.reserved

    def can_afford(self, amount: float) -> bool:
        if self.max_inr is None:
            return True
        return self.total + self.reserved + amount <= self.max_inr + 1e-9

    def reserve(self, amount: float) -> None:
        with self._lock:
            if not self.can_afford(amount):
                raise BudgetExceeded("insufficient budget for reservation")
            self.reserved += amount

    def settle(self, model: str, reserved: float, actual: float = 0.0,
               estimated: bool = False) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reserved)
            # Account for the charge even when the provider exceeds its stated limit.
            self.spent_by_model[model] = self.spent_by_model.get(model, 0.0) + actual
            if estimated:
                self.estimated_flag[model] = True

    def charge(self, model: str, amount: float, estimated: bool = False) -> None:
        """Record spend. Raises BudgetExceeded if it would cross the cap (caller reserves first)."""
        with self._lock:
            if self.max_inr is not None and self.total + amount > self.max_inr + 1e-9:
                raise BudgetExceeded(
                    f"would spend {self.total + amount:.4f} > cap {self.max_inr:.4f} INR"
                )
            self.spent_by_model[model] = self.spent_by_model.get(model, 0.0) + amount
            if estimated:
                self.estimated_flag[model] = True

    def summary(self) -> dict:
        return {
            "total_inr": round(self.total, 6),
            "by_model": {k: round(v, 6) for k, v in sorted(self.spent_by_model.items())},
            "estimated": {k: True for k in sorted(self.estimated_flag)},
            "max_inr": self.max_inr,
        }


# ---- rate helpers (rates come from config/bodhan_api.yaml) ----


def translate_cost_inr(output_tokens: int, rate_per_10k: float) -> float:
    return (output_tokens / 10_000.0) * rate_per_10k


def speak_cost_inr(input_chars: int, rate_per_10k: float) -> float:
    return (input_chars / 10_000.0) * rate_per_10k


def transcribe_cost_inr(audio_seconds: float, rate_per_minute: float) -> float:
    return (audio_seconds / 60.0) * rate_per_minute
