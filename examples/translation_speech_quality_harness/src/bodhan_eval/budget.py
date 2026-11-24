"""Cost ledger and the --max-cost-inr guard.

Two numbers are tracked separately:
* `attributed`: what the outputs used in this report cost, whether or not they came from cache.
  It is deterministic, so a cached rerun produces an identical report.
* `spent`: what this process actually paid (cache misses only). The budget guard uses this.
"""
from __future__ import annotations

import threading
from collections import defaultdict

# Hosted prices from the assignment (INR).
PRICE_TRANSLATE_PER_TOKEN = 0.20 / 10_000  # output tokens
PRICE_SPEAK_PER_CHAR = 6.0 / 10_000  # input characters
PRICE_TRANSCRIBE_PER_MIN = 0.10  # audio minutes


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, max_inr: float | None = None):
        self.max_inr = max_inr
        self._lock = threading.Lock()
        self.attributed: dict[str, float] = defaultdict(float)
        self.spent: dict[str, float] = defaultdict(float)
        self.units: dict[str, float] = defaultdict(float)  # tokens / chars / minutes / llm tokens

    @property
    def spent_total(self) -> float:
        return sum(self.spent.values())

    def reserve(self, estimate_inr: float) -> None:
        """Raise BudgetExceeded before a paid call that would cross the cap."""
        if self.max_inr is None:
            return
        with self._lock:
            if self.spent_total + estimate_inr > self.max_inr:
                raise BudgetExceeded(f"spending ₹{estimate_inr:.3f} would exceed --max-cost-inr {self.max_inr} (spent ₹{self.spent_total:.3f})")

    def record(self, service: str, inr: float, units: float, unit_name: str, cached: bool) -> None:
        with self._lock:
            self.attributed[service] += inr
            self.units[f"{service}.{unit_name}"] += units
            if not cached:
                self.spent[service] += inr

    def record_llm(self, prompt_tokens: int, completion_tokens: int, cached: bool) -> None:
        with self._lock:
            self.units["llm.prompt_tokens"] += prompt_tokens
            self.units["llm.completion_tokens"] += completion_tokens
            if not cached:
                self.units["llm.uncached_calls"] += 1

    def summary(self) -> dict:
        return {
            "attributed_inr": {k: round(v, 4) for k, v in sorted(self.attributed.items())},
            "attributed_total_inr": round(sum(self.attributed.values()), 4),
            "units": {k: round(v, 3) for k, v in sorted(self.units.items()) if not k.endswith("uncached_calls")},
        }

    def run_summary(self) -> dict:
        return {
            "spent_inr": {k: round(v, 4) for k, v in sorted(self.spent.items())},
            "spent_total_inr": round(self.spent_total, 4),
            "llm_uncached_calls": int(self.units.get("llm.uncached_calls", 0)),
        }
