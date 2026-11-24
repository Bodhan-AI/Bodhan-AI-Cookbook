"""Spend estimation and the ``--max-cost-inr`` budget guard.

Bodhan does not publish per-unit prices in this repository, so the rates
below are **placeholders** that you must set from your console pricing via
environment variables. The guard is still useful with placeholder rates: it
is conservative (it reserves an estimate *before* each paid call, and cache
hits cost nothing) and it records actual usage after each call.

Billing units follow the Bodhan API reference:

* Indic-Translate: output tokens are billed, input tokens are free.
* Indic-Transcribe: billed per second of audio.
* Indic-Speak: billed per input character (assumption, configurable).
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field


class BudgetExceededError(RuntimeError):
    """Raised when a paid call would push spend past ``--max-cost-inr``."""


def _rate(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


@dataclass
class CostRates:
    translate_inr_per_1k_output_tokens: float = 2.0
    speak_inr_per_1k_chars: float = 2.0
    transcribe_inr_per_minute: float = 1.0
    configured: bool = False

    @classmethod
    def from_env(cls) -> CostRates:
        names = [
            "BODHAN_TRANSLATE_INR_PER_1K_OUTPUT_TOKENS",
            "BODHAN_SPEAK_INR_PER_1K_CHARS",
            "BODHAN_TRANSCRIBE_INR_PER_MINUTE",
        ]
        defaults = cls()
        return cls(
            translate_inr_per_1k_output_tokens=_rate(names[0], defaults.translate_inr_per_1k_output_tokens),
            speak_inr_per_1k_chars=_rate(names[1], defaults.speak_inr_per_1k_chars),
            transcribe_inr_per_minute=_rate(names[2], defaults.transcribe_inr_per_minute),
            configured=any(os.getenv(name) for name in names),
        )


@dataclass
class CostTracker:
    max_inr: float | None
    rates: CostRates = field(default_factory=CostRates.from_env)
    spent_inr: float = 0.0
    calls: dict = field(default_factory=dict)
    blocked_calls: int = 0

    # -- estimates ---------------------------------------------------------

    def estimate_translate(self, text: str) -> float:
        # Conservative: assume up to one output token per input character.
        tokens = max(16, len(text))
        return tokens / 1000 * self.rates.translate_inr_per_1k_output_tokens

    def estimate_speak(self, text: str) -> float:
        return len(text) / 1000 * self.rates.speak_inr_per_1k_chars

    def estimate_transcribe(self, text: str) -> float:
        # Roughly 10 characters of Indic text per second of speech, + 1 s.
        seconds = len(text) / 10 + 1
        return seconds / 60 * self.rates.transcribe_inr_per_minute

    # -- actual costs ------------------------------------------------------

    def translate_cost(self, usage: dict, text: str) -> float:
        tokens = (usage or {}).get("completion_tokens")
        if tokens is None:
            return self.estimate_translate(text)
        return tokens / 1000 * self.rates.translate_inr_per_1k_output_tokens

    def transcribe_cost(self, seconds: float) -> float:
        return seconds / 60 * self.rates.transcribe_inr_per_minute

    # -- guard ---------------------------------------------------------------

    def reserve(self, service: str, estimate_inr: float) -> None:
        """Refuse a call whose estimate would exceed the budget."""
        if self.max_inr is None:
            return
        if self.spent_inr + estimate_inr > self.max_inr + 1e-9:
            self.blocked_calls += 1
            raise BudgetExceededError(
                f"{service} call (est. ₹{estimate_inr:.4f}) would exceed "
                f"budget ₹{self.max_inr:.2f} (spent ₹{self.spent_inr:.4f})"
            )

    def record(self, service: str, actual_inr: float) -> None:
        self.spent_inr += actual_inr
        entry = self.calls.setdefault(service, {"calls": 0, "inr": 0.0})
        entry["calls"] += 1
        entry["inr"] += actual_inr

    def summary(self) -> dict:
        return {
            "max_inr": self.max_inr,
            "estimated_spend_inr": round(self.spent_inr, 4),
            "by_service": {
                name: {"calls": v["calls"], "inr": round(v["inr"], 4)}
                for name, v in sorted(self.calls.items())
            },
            "blocked_calls": self.blocked_calls,
            "budget_exhausted": self.blocked_calls > 0,
            "rates": asdict(self.rates),
            "note": (
                "Spend is estimated from configured per-unit rates."
                if self.rates.configured else
                "Spend is estimated from PLACEHOLDER rates; set the "
                "BODHAN_*_INR_* variables from your console pricing."
            ),
        }
