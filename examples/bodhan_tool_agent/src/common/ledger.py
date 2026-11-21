"""Bodhan rupee cost estimator and estimated-budget circuit breaker.

Estimates Bodhan model charges using published unit rates:
- indic-transcribe: ₹0.10 / min (billed per second)
- indic-translate: ₹0.20 / 10,000 output tokens (input tokens free)
- indic-speak: ₹6.00 / 10,000 input characters
- indic-ocr: ₹0.20 / image or page

These local estimates are not provider-reported actual billing and do not include
OpenAI-compatible LLM or gateway charges.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

ModelType = Literal["indic-transcribe", "indic-translate", "indic-speak", "indic-ocr", "llm"]

# Pricing constants in INR (₹)
PRICE_SOURCE_URL = "https://console.bodhan.ai/"
PRICE_VERIFIED_ON = "2026-11-19"
PRICE_TRANSCRIBE_PER_MIN_INR = 0.10
PRICE_TRANSLATE_PER_10K_TOKENS_INR = 0.20
PRICE_SPEAK_PER_10K_CHARS_INR = 6.00
PRICE_OCR_PER_PAGE_INR = 0.20


@dataclass
class CostLedger:
    """Track rounded Bodhan charge estimates; this is not an invoice ledger."""

    max_budget_inr: float = 20.0
    cumulative_cost_inr: float = 0.0
    history: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def calculate_cost(model: ModelType, units: float) -> float:
        """Calculate the cost in INR for a given model and unit quantity.
        
        Units:
        - indic-transcribe: audio duration in seconds
        - indic-translate: output token count
        - indic-speak: character count
        - indic-ocr: page / image count
        - llm: estimated cost in INR (optional)
        """
        if units < 0:
            raise ValueError(f"Units cannot be negative: {units}")

        if model == "indic-transcribe":
            # Billed per second at ₹0.10 per minute
            return round((units / 60.0) * PRICE_TRANSCRIBE_PER_MIN_INR, 4)
        elif model == "indic-translate":
            # ₹0.20 per 10,000 output tokens
            return round((units / 10000.0) * PRICE_TRANSLATE_PER_10K_TOKENS_INR, 4)
        elif model == "indic-speak":
            # ₹6.00 per 10,000 characters
            return round((units / 10000.0) * PRICE_SPEAK_PER_10K_CHARS_INR, 4)
        elif model == "indic-ocr":
            # ₹0.20 per image / page
            return round(units * PRICE_OCR_PER_PAGE_INR, 4)
        elif model == "llm":
            return round(units, 4)
        raise ValueError(f"Unsupported cost model: {model}")

    def can_afford(self, model: ModelType, units: float) -> bool:
        """Check whether an estimated operation fits within the remaining budget."""
        projected = self.calculate_cost(model, units)
        return (self.cumulative_cost_inr + projected) <= self.max_budget_inr

    def record_operation(
        self,
        model: ModelType,
        units: float,
        step: int,
        details: str = ""
    ) -> float:
        """Record an operation, add its cost, and return the operation's cost."""
        cost = self.calculate_cost(model, units)
        self.cumulative_cost_inr = round(self.cumulative_cost_inr + cost, 4)
        self.history.append({
            "step": step,
            "model": model,
            "units": units,
            "cost_inr": cost,
            "cumulative_cost_inr": self.cumulative_cost_inr,
            "details": details,
        })
        return cost

    @property
    def remaining_budget_inr(self) -> float:
        return max(0.0, round(self.max_budget_inr - self.cumulative_cost_inr, 4))

    @property
    def is_exhausted(self) -> bool:
        return self.cumulative_cost_inr >= self.max_budget_inr
