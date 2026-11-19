"""Prices, pre-call estimators and a per-call spending cap. All amounts are rupees."""
from __future__ import annotations

import math
import threading

from .errors import BodhanError

ASR_PER_MIN = 0.10
MT_PER_10K_TOKENS = 0.20
TTS_PER_10K_CHARS = 6.0
OCR_PER_IMAGE = 0.20
MT_TOKENS_PER_SOURCE_CHAR = 1.2  # conservative: Indic output runs ~1.2x source chars at ~1 token/char


def _up(x: float) -> float:
    return math.ceil(x * 100 - 1e-9) / 100


def asr_cost(duration_s: float) -> float:
    return _up(duration_s / 60 * ASR_PER_MIN)


def mt_cost(output_tokens: int) -> float:
    return output_tokens / 10_000 * MT_PER_10K_TOKENS


def mt_estimate(source_chars: int) -> float:
    return _up(source_chars * MT_TOKENS_PER_SOURCE_CHAR / 10_000 * MT_PER_10K_TOKENS)


def tts_cost(chars: int) -> float:
    return chars / 10_000 * TTS_PER_10K_CHARS


def ocr_cost(pages: int) -> float:
    return pages * OCR_PER_IMAGE


class CallBudget:
    """A ceiling for one tool call. `reserve` refuses before any money is spent."""

    def __init__(self, cap: float | None) -> None:
        self.cap = cap
        self.spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float, what: str) -> None:
        with self._lock:
            if self.cap is not None and self.spent + amount > self.cap + 1e-9:
                raise BodhanError(
                    "BUDGET_EXCEEDED",
                    f"{what} needs about Rs {amount:.2f} but only Rs {max(self.cap - self.spent, 0):.2f} of max_cost_inr remains. Nothing was spent.",
                    hint="Raise max_cost_inr or reduce the input.",
                )

    def add(self, amount: float) -> None:
        with self._lock:
            self.spent += amount
