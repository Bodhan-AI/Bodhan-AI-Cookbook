"""Cost accounting and the --max-cost-inr budget guard.

Prices (hosted Bodhan API, from the assignment):
  Indic-Translate   ₹0.20 per 10,000 output tokens
  Indic-Speak       ₹6    per 10,000 input characters
  Indic-Transcribe  ₹0.10 per input audio minute (billed per second)

Paid calls reserve an estimate before they start, so concurrent calls cannot overshoot
the budget together; the reservation is settled with the actual cost afterwards.
Cache hits cost nothing and are counted separately.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

PRICE_TRANSLATE_PER_TOKEN = 0.20 / 10_000
PRICE_SPEAK_PER_CHAR = 6.0 / 10_000
PRICE_TRANSCRIBE_PER_SECOND = 0.10 / 60


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class ServiceStats:
    calls: int = 0
    cached: int = 0
    units: float = 0.0  # tokens / chars / seconds
    inr: float = 0.0


@dataclass
class CostMeter:
    max_inr: float | None = None
    llm_price_in_per_m: float = field(
        default_factory=lambda: float(os.environ.get("LLM_PRICE_INR_PER_M_INPUT", "0") or 0)
    )
    llm_price_out_per_m: float = field(
        default_factory=lambda: float(os.environ.get("LLM_PRICE_INR_PER_M_OUTPUT", "0") or 0)
    )

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self.stats: dict[str, ServiceStats] = {}
        self.reserved = 0.0
        self.llm_tokens_in = 0
        self.llm_tokens_out = 0

    @property
    def spent(self) -> float:
        return sum(s.inr for s in self.stats.values())

    def remaining(self) -> float | None:
        if self.max_inr is None:
            return None
        return self.max_inr - self.spent - self.reserved

    def reserve(self, service: str, estimate_inr: float) -> float:
        """Reserve budget for a paid call; raises BudgetExceeded if it would not fit."""
        with self._lock:
            if (
                self.max_inr is not None
                and self.spent + self.reserved + estimate_inr > self.max_inr
            ):
                raise BudgetExceeded(
                    f"{service}: estimated ₹{estimate_inr:.4f} exceeds remaining budget "
                    f"₹{self.max_inr - self.spent - self.reserved:.4f}"
                )
            self.reserved += estimate_inr
            return estimate_inr

    def settle(self, service: str, reservation: float, units: float, inr: float) -> None:
        with self._lock:
            self.reserved = max(0.0, self.reserved - reservation)
            s = self.stats.setdefault(service, ServiceStats())
            s.calls += 1
            s.units += units
            s.inr += inr

    def release(self, reservation: float) -> None:
        """Give back a reservation for a call that failed without being billed."""
        with self._lock:
            self.reserved = max(0.0, self.reserved - reservation)

    def record_cached(self, service: str) -> None:
        with self._lock:
            self.stats.setdefault(service, ServiceStats()).cached += 1

    def record_llm(self, tokens_in: int, tokens_out: int, cached: bool) -> None:
        with self._lock:
            s = self.stats.setdefault("llm", ServiceStats())
            if cached:
                s.cached += 1
                return
            s.calls += 1
            s.units += tokens_in + tokens_out
            s.inr += (
                tokens_in * self.llm_price_in_per_m + tokens_out * self.llm_price_out_per_m
            ) / 1e6
            self.llm_tokens_in += tokens_in
            self.llm_tokens_out += tokens_out

    def summary(self) -> dict:
        return {
            "total_inr": round(self.spent, 4),
            "max_inr": self.max_inr,
            "services": {
                k: {
                    "calls": v.calls,
                    "cached": v.cached,
                    "units": round(v.units, 2),
                    "inr": round(v.inr, 4),
                }
                for k, v in sorted(self.stats.items())
            },
            "llm_tokens": {"input": self.llm_tokens_in, "output": self.llm_tokens_out},
        }


def estimate_translate_inr(text: str) -> float:
    # Upper-ish bound: Indic scripts tokenize at roughly 1 token per 1-2 chars.
    return len(text) * 1.5 * PRICE_TRANSLATE_PER_TOKEN


def speak_inr(text: str) -> float:
    return len(text) * PRICE_SPEAK_PER_CHAR


def transcribe_inr(seconds: float) -> float:
    return max(1.0, seconds) * PRICE_TRANSCRIBE_PER_SECOND
