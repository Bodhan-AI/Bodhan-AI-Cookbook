"""Pricing definitions and budget management for Bodhan models.

Rates:
- Indic-Transcribe: ₹0.10 per input audio minute
- Indic-Translate: ₹0.20 per 10,000 output tokens
- Indic-Speak: ₹6.00 per 10,000 input characters
- Indic-OCR: ₹0.20 per image / page
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading


class BudgetExceededError(Exception):
    """Raised when an operation would exceed the allocated budget."""


# Official rates in INR
RATE_TRANSCRIBE_PER_MIN = 0.10
RATE_TRANSLATE_PER_10K_TOKENS = 0.20
RATE_SPEAK_PER_10K_CHARS = 6.00
RATE_OCR_PER_IMAGE = 0.20


def calculate_transcribe_cost(duration_seconds: float) -> float:
    """Calculates ASR cost given duration in seconds."""
    if duration_seconds <= 0:
        return 0.0
    return (duration_seconds / 60.0) * RATE_TRANSCRIBE_PER_MIN


def calculate_translate_cost(output_tokens: int) -> float:
    """Calculates translation cost given output tokens."""
    if output_tokens <= 0:
        return 0.0
    return (output_tokens / 10000.0) * RATE_TRANSLATE_PER_10K_TOKENS


def calculate_speak_cost(input_char_count: int) -> float:
    """Calculates TTS cost given input character count."""
    if input_char_count <= 0:
        return 0.0
    return (input_char_count / 10000.0) * RATE_SPEAK_PER_10K_CHARS


def calculate_ocr_cost(image_count: int = 1) -> float:
    """Calculates OCR cost given number of images/pages."""
    if image_count <= 0:
        return 0.0
    return image_count * RATE_OCR_PER_IMAGE


@dataclass
class CostRecord:
    model_id: str
    cost_inr: float
    description: str


class BudgetManager:
    """Thread-safe budget manager enforcing maximum spend in INR."""

    def __init__(self, max_cost_inr: float = 20.0):
        self.max_cost_inr = max_cost_inr
        self.spent_inr = 0.0
        self.records: list[CostRecord] = []
        self._lock = threading.Lock()

    @property
    def remaining_inr(self) -> float:
        with self._lock:
            return max(0.0, self.max_cost_inr - self.spent_inr)

    def can_afford(self, estimated_cost: float) -> bool:
        with self._lock:
            return (self.spent_inr + estimated_cost) <= self.max_cost_inr

    def check_budget(self, estimated_cost: float) -> None:
        with self._lock:
            if (self.spent_inr + estimated_cost) > self.max_cost_inr:
                raise BudgetExceededError(
                    f"Estimated cost ₹{estimated_cost:.4f} exceeds remaining budget ₹{self.max_cost_inr - self.spent_inr:.4f} "
                    f"(Total spent: ₹{self.spent_inr:.4f} / Limit: ₹{self.max_cost_inr:.2f})"
                )

    def record_cost(self, model_id: str, cost: float, description: str = "") -> None:
        with self._lock:
            self.spent_inr += cost
            self.records.append(CostRecord(model_id=model_id, cost_inr=cost, description=description))

    def get_summary(self) -> dict:
        with self._lock:
            return {
                "max_cost_inr": self.max_cost_inr,
                "spent_inr": round(self.spent_inr, 4),
                "remaining_inr": round(max(0.0, self.max_cost_inr - self.spent_inr), 4),
                "calls_count": len(self.records),
            }
