"""Rupee cost ledger. Budget enforcement is deterministic code, never an LLM decision."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from .errors import BudgetExceeded

INR_PER_AUDIO_MIN = 0.10
INR_PER_10K_OUTPUT_TOKENS = 0.20
INR_PER_10K_SPEAK_CHARS = 6.0
INR_PER_OCR_IMAGE = 0.20
# Indic scripts tokenise poorly; be conservative when the API does not report usage.
EST_CHARS_PER_TOKEN = 2.0


def est_transcribe(seconds: float) -> float:
    return seconds / 60.0 * INR_PER_AUDIO_MIN


def est_translate(output_chars: int) -> float:
    return output_chars / EST_CHARS_PER_TOKEN / 10_000 * INR_PER_10K_OUTPUT_TOKENS


def est_speak(chars: int) -> float:
    return chars / 10_000 * INR_PER_10K_SPEAK_CHARS


def est_ocr(images: int = 1) -> float:
    return images * INR_PER_OCR_IMAGE


class CostLedger:
    """Thread-safe. `reserve` raises BEFORE a paid call that could push spend past the cap."""

    def __init__(self, max_inr: float | None = None):
        self.max_inr = max_inr
        self._lock = threading.Lock()
        self.spent = 0.0
        self._reserved = 0.0
        self.by_model: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.cache_hits = 0

    def reserve(self, estimate: float) -> float:
        with self._lock:
            if self.max_inr is not None and self.spent + self._reserved + estimate > self.max_inr + 1e-9:
                remaining = max(self.max_inr - self.spent - self._reserved, 0.0)
                raise BudgetExceeded(
                    f"Next call needs about Rs {estimate:.3f} but only Rs {remaining:.3f} of the "
                    f"Rs {self.max_inr:.2f} budget remains.",
                    spent_inr=round(self.spent, 4),
                    max_inr=self.max_inr,
                )
            self._reserved += estimate
            return estimate

    def settle(self, model: str, reserved: float, actual: float | None = None) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - reserved)
            cost = reserved if actual is None else actual
            self.spent += cost
            self.by_model[model] += cost
            self.calls[model] += 1

    def release(self, reserved: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - reserved)

    def note_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def remaining(self) -> float:
        return float("inf") if self.max_inr is None else self.max_inr - self.spent - self._reserved

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_inr": round(self.spent, 4),
                "max_inr": self.max_inr,
                "by_model_inr": {k: round(v, 4) for k, v in self.by_model.items()},
                "calls": dict(self.calls),
                "cache_hits": self.cache_hits,
            }
