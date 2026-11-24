"""Hosted prices (INR) and a per-process usage ledger.

Prices from the assignment brief:
  Indic-Transcribe  Rs 0.10 per input audio minute (billed per second)
  Indic-Translate   Rs 0.20 per 10,000 output tokens (input tokens are free)
  Indic-Speak       Rs 6 per 10,000 input characters
  Indic-OCR         Rs 0.20 per image
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

TRANSCRIBE_INR_PER_SECOND = 0.10 / 60
TRANSLATE_INR_PER_OUTPUT_TOKEN = 0.20 / 10_000
SPEAK_INR_PER_CHAR = 6.0 / 10_000
OCR_INR_PER_IMAGE = 0.20


def transcribe_cost(seconds: float) -> float:
    return math.ceil(max(0.0, seconds)) * TRANSCRIBE_INR_PER_SECOND


def translate_cost(output_tokens: int) -> float:
    return max(0, output_tokens) * TRANSLATE_INR_PER_OUTPUT_TOKEN


def speak_cost(chars: int) -> float:
    return max(0, chars) * SPEAK_INR_PER_CHAR


def ocr_cost(images: int) -> float:
    return max(0, images) * OCR_INR_PER_IMAGE


def estimate_translate_tokens(input_chars: int) -> int:
    # Deliberately pessimistic: Indic scripts often tokenise at ~1 token per 1-2 characters.
    return int(input_chars * 1.5) + 50


def speech_seconds_estimate(chars: int) -> float:
    # Roughly 12 characters of Indic text per second of speech.
    return chars / 12.0


@dataclass
class ModelUsage:
    calls: int = 0
    cached: int = 0
    failed: int = 0
    units: float = 0.0
    cost_inr: float = 0.0


UNIT_NAMES = {
    "indic-transcribe": "audio_seconds",
    "indic-translate": "output_tokens",
    "indic-speak": "input_chars",
    "indic-ocr": "images",
}


@dataclass
class Ledger:
    """Cumulative usage for this server process (exposed as the bodhan://usage resource)."""

    models: dict[str, ModelUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, *, units: float = 0.0, cost: float = 0.0, cached: bool = False, failed: bool = False) -> None:
        with self._lock:
            u = self.models.setdefault(model, ModelUsage())
            if failed:
                u.failed += 1
                return
            if cached:
                u.cached += 1
                return
            u.calls += 1
            u.units += units
            u.cost_inr += cost

    def total(self) -> float:
        return sum(u.cost_inr for u in self.models.values())

    def snapshot(self) -> dict:
        return {
            "total_cost_inr": round(self.total(), 4),
            "models": {
                m: {
                    "billed_calls": u.calls, "cache_hits": u.cached, "failed_calls": u.failed,
                    UNIT_NAMES.get(m, "units"): round(u.units, 2), "cost_inr": round(u.cost_inr, 4),
                }
                for m, u in self.models.items()
            },
        }


class CallMeter:
    """Collects the cost of the model calls made while serving one tool call."""

    def __init__(self) -> None:
        self.cost_inr = 0.0
        self.billed_calls = 0
        self.cache_hits = 0
        self.retries = 0
        self.by_model: dict[str, float] = {}

    def add(self, model: str, cost: float, cached: bool, retries: int = 0) -> None:
        self.retries += retries
        if cached:
            self.cache_hits += 1
            return
        self.billed_calls += 1
        self.cost_inr += cost
        self.by_model[model] = self.by_model.get(model, 0.0) + cost

    def summary(self) -> dict:
        return {
            "cost_inr": round(self.cost_inr, 4),
            "billed_calls": self.billed_calls,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
        }
