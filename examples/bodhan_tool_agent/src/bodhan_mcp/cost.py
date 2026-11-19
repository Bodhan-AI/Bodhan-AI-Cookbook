from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from bodhan_mcp.config import (
    PRICE_OCR_INR_PER_IMAGE,
    PRICE_SPEAK_INR_PER_10K_INPUT_CHARS,
    PRICE_TRANSCRIBE_INR_PER_MINUTE,
    PRICE_TRANSLATE_INR_PER_10K_OUTPUT_TOKENS,
)


class BudgetExceededError(Exception):
    def __init__(
        self,
        message: str,
        current_cost_inr: float,
        max_cost_inr: float,
        estimated_addition_inr: float = 0.0,
        budget_type: str = "cost_inr",
    ) -> None:
        super().__init__(message)
        self.current_cost_inr = current_cost_inr
        self.max_cost_inr = max_cost_inr
        self.estimated_addition_inr = estimated_addition_inr
        self.budget_type = budget_type


@dataclass
class CostEntry:
    model: str
    operation: str
    units: float
    unit_name: str
    cost_inr: float
    cached: bool = False
    latency_ms: float = 0.0


@dataclass
class CostTracker:
    max_cost_inr: Optional[float] = None
    max_steps: Optional[int] = None
    total_cost_inr: float = 0.0
    steps_taken: int = 0
    cache_hits: int = 0
    api_calls: int = 0
    entries: List[CostEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @staticmethod
    def estimate_transcribe_cost(duration_seconds: float) -> float:
        minutes = max(0.0, duration_seconds) / 60.0
        return round(minutes * PRICE_TRANSCRIBE_INR_PER_MINUTE, 6)

    @staticmethod
    def estimate_translate_cost(output_tokens: int) -> float:
        return round((max(0, output_tokens) / 10000.0) * PRICE_TRANSLATE_INR_PER_10K_OUTPUT_TOKENS, 6)

    @staticmethod
    def estimate_speak_cost(char_count: int) -> float:
        return round((max(0, char_count) / 10000.0) * PRICE_SPEAK_INR_PER_10K_INPUT_CHARS, 6)

    @staticmethod
    def estimate_ocr_cost(image_count: int = 1) -> float:
        return round(max(0, image_count) * PRICE_OCR_INR_PER_IMAGE, 6)

    def check_step_budget(self) -> None:
        with self._lock:
            if self.max_steps is not None and self.steps_taken >= self.max_steps:
                raise BudgetExceededError(
                    f"Step budget exhausted: reached {self.steps_taken}/{self.max_steps} maximum steps.",
                    current_cost_inr=self.total_cost_inr,
                    max_cost_inr=self.max_cost_inr or 0.0,
                    budget_type="steps",
                )

    def increment_step(self) -> int:
        self.check_step_budget()
        with self._lock:
            self.steps_taken += 1
            return self.steps_taken

    def check_cost_budget(self, estimated_cost_inr: float, description: str = "") -> None:
        with self._lock:
            if self.max_cost_inr is not None:
                projected = round(self.total_cost_inr + estimated_cost_inr, 6)
                if projected > self.max_cost_inr + 1e-9:
                    desc_suffix = f" for {description}" if description else ""
                    raise BudgetExceededError(
                        f"Cost budget exceeded{desc_suffix}: current cost is ₹{self.total_cost_inr:.4f}, "
                        f"estimated operation cost is ₹{estimated_cost_inr:.4f} (projected ₹{projected:.4f} > "
                        f"budget ₹{self.max_cost_inr:.4f}). Stopping cleanly.",
                        current_cost_inr=self.total_cost_inr,
                        max_cost_inr=self.max_cost_inr,
                        estimated_addition_inr=estimated_cost_inr,
                        budget_type="cost_inr",
                    )

    def record(
        self,
        model: str,
        operation: str,
        units: float,
        unit_name: str,
        cost_inr: float,
        cached: bool = False,
        latency_ms: float = 0.0,
    ) -> float:
        actual_cost = 0.0 if cached else round(cost_inr, 6)
        with self._lock:
            if not cached:
                self.total_cost_inr = round(self.total_cost_inr + actual_cost, 6)
                self.api_calls += 1
            else:
                self.cache_hits += 1
            self.entries.append(
                CostEntry(
                    model=model,
                    operation=operation,
                    units=units,
                    unit_name=unit_name,
                    cost_inr=actual_cost,
                    cached=cached,
                    latency_ms=round(latency_ms, 2),
                )
            )
        return actual_cost

    def summary(self) -> Dict[str, object]:
        with self._lock:
            by_model: Dict[str, float] = {}
            for entry in self.entries:
                by_model[entry.model] = round(by_model.get(entry.model, 0.0) + entry.cost_inr, 6)
            return {
                "total_cost_inr": round(self.total_cost_inr, 6),
                "max_cost_inr": self.max_cost_inr,
                "remaining_cost_inr": (
                    round(max(0.0, self.max_cost_inr - self.total_cost_inr), 6)
                    if self.max_cost_inr is not None
                    else None
                ),
                "steps_taken": self.steps_taken,
                "max_steps": self.max_steps,
                "api_calls": self.api_calls,
                "cache_hits": self.cache_hits,
                "cost_by_model_inr": by_model,
            }
