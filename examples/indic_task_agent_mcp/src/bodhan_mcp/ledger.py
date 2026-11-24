"""Cost ledger and budget guard.

#Plan
- Every Bodhan call asks `reserve()` with an estimate first; if spent + estimate would pass the cap, the call is
  refused with `budget_exceeded` before any money is spent.
- After the call, `record()` books the actual cost (or 0 on a cache hit) per model, so reports can show both.
- The ledger also counts how often an identical request was answered from cache, which is the evidence for the
  "never send the same input to the same model twice" target.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from . import config
from .errors import ToolError


def estimate_transcribe(seconds: float) -> float:
    return seconds / 60.0 * config.PRICE_TRANSCRIBE_PER_MIN


def estimate_translate(output_tokens: int) -> float:
    return output_tokens / 10_000 * config.PRICE_TRANSLATE_PER_10K_OUT_TOKENS


def estimate_speak(chars: int) -> float:
    return chars / 10_000 * config.PRICE_SPEAK_PER_10K_CHARS


def estimate_ocr(images: int) -> float:
    return images * config.PRICE_OCR_PER_IMAGE


@dataclass
class CallRecord:
    model: str
    cost_inr: float
    cache_hit: bool
    units: float
    unit: str


@dataclass
class Ledger:
    max_cost_inr: float | None = None
    calls: list[CallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _reserved: float = 0.0

    @property
    def spent(self) -> float:
        return sum(c.cost_inr for c in self.calls)

    @property
    def remaining(self) -> float | None:
        return None if self.max_cost_inr is None else max(0.0, self.max_cost_inr - self.spent - self._reserved)

    def reserve(self, model: str, estimate: float) -> None:
        with self._lock:
            if self.max_cost_inr is not None and self.spent + self._reserved + estimate > self.max_cost_inr + 1e-9:
                raise ToolError(
                    "budget_exceeded",
                    f"This {model} call would cost about ₹{estimate:.3f}; only ₹{max(0.0, self.max_cost_inr - self.spent - self._reserved):.3f} "
                    f"of the ₹{self.max_cost_inr:.2f} budget is left.",
                    hint="Stop and report what was finished, or rerun with a larger --max-cost-inr.",
                    details={"estimate_inr": round(estimate, 4), "spent_inr": round(self.spent, 4)},
                )
            self._reserved += estimate

    def release(self, estimate: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - estimate)

    def record(self, model: str, cost: float, *, cache_hit: bool, units: float, unit: str) -> None:
        with self._lock:
            self.calls.append(CallRecord(model, 0.0 if cache_hit else cost, cache_hit, units, unit))

    def summary(self) -> dict:
        by_model: dict[str, dict] = {}
        for c in self.calls:
            m = by_model.setdefault(c.model, {"calls": 0, "cache_hits": 0, "cost_inr": 0.0, "units": 0.0, "unit": c.unit})
            m["calls"] += 1
            m["cache_hits"] += int(c.cache_hit)
            m["cost_inr"] += c.cost_inr
            m["units"] += 0 if c.cache_hit else c.units
        for m in by_model.values():
            m["cost_inr"] = round(m["cost_inr"], 4)
            m["units"] = round(m["units"], 2)
        return {"total_inr": round(self.spent, 4), "max_cost_inr": self.max_cost_inr, "by_model": by_model}
