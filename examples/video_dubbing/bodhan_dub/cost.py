"""Rupee accounting.

Prices are the hosted rates published with the assignment, per the Bodhan
console. They are expressed per unit here so a call can be charged exactly:

* ``indic-transcribe`` — ₹0.10 per input audio minute
* ``indic-translate``  — ₹0.20 per 10,000 output tokens
* ``indic-speak``      — ₹6.00 per 10,000 input characters
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

INR_PER_AUDIO_MINUTE = 0.10
INR_PER_10K_OUTPUT_TOKENS = 0.20
INR_PER_10K_CHARS = 6.00


def transcribe_cost_inr(audio_seconds: float) -> float:
    return max(0.0, audio_seconds) * INR_PER_AUDIO_MINUTE / 60.0


def translate_cost_inr(output_tokens: int) -> float:
    return max(0, output_tokens) * INR_PER_10K_OUTPUT_TOKENS / 10_000.0


def speak_cost_inr(characters: int) -> float:
    return max(0, characters) * INR_PER_10K_CHARS / 10_000.0


@dataclass
class CostLine:
    stage: str
    model: str
    amount_inr: float
    units: dict[str, float] = field(default_factory=dict)
    cached: bool = False


class CostMeter:
    """Thread-safe running total, so parallel calls cannot lose a charge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lines: list[CostLine] = []
        self._started = datetime.now(timezone.utc)

    def add(
        self,
        stage: str,
        model: str,
        amount_inr: float,
        *,
        units: dict[str, float] | None = None,
        cached: bool = False,
    ) -> None:
        with self._lock:
            self._lines.append(
                CostLine(
                    stage=stage,
                    model=model,
                    amount_inr=0.0 if cached else round(amount_inr, 6),
                    units=units or {},
                    cached=cached,
                )
            )

    def charge_transcribe(self, audio_seconds: float, *, stage: str = "asr", cached: bool = False) -> None:
        self.add(
            stage,
            "indic-transcribe",
            transcribe_cost_inr(audio_seconds),
            units={"audio_seconds": round(audio_seconds, 3)},
            cached=cached,
        )

    def charge_translate(self, output_tokens: int, *, stage: str = "translate", cached: bool = False) -> None:
        self.add(
            stage,
            "indic-translate",
            translate_cost_inr(output_tokens),
            units={"output_tokens": float(output_tokens)},
            cached=cached,
        )

    def charge_speak(self, characters: int, *, stage: str = "speak", cached: bool = False) -> None:
        self.add(
            stage,
            "indic-speak",
            speak_cost_inr(characters),
            units={"characters": float(characters)},
            cached=cached,
        )

    @property
    def total_inr(self) -> float:
        with self._lock:
            return round(sum(line.amount_inr for line in self._lines), 6)

    def by_stage(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        with self._lock:
            for line in self._lines:
                totals[line.stage] = round(totals.get(line.stage, 0.0) + line.amount_inr, 6)
        return totals

    def summary(self) -> dict[str, object]:
        with self._lock:
            lines = list(self._lines)
        return {
            "currency": "INR",
            "total_inr": round(sum(line.amount_inr for line in lines), 6),
            "by_stage_inr": self.by_stage(),
            "calls": len(lines),
            "cached_calls": sum(1 for line in lines if line.cached),
            "audio_seconds": round(sum(line.units.get("audio_seconds", 0.0) for line in lines), 3),
            "characters": int(sum(line.units.get("characters", 0.0) for line in lines)),
            "output_tokens": int(sum(line.units.get("output_tokens", 0.0) for line in lines)),
        }
