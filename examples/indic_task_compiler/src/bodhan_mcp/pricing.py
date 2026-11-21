"""Deterministic cost math and optional cumulative dev spend ledger."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bodhan_mcp.rules import PRICING_INR


def estimate_output_tokens(text: str) -> int:
    """Estimate Indic-Translate output tokens conservatively from input characters."""
    if not text:
        return 0
    # Indic UTF-8 text averages ~2-3 chars/token; 0.5 tokens/char is a safe estimate.
    return max(1, int(len(text) * 0.5))


def cost_transcribe(duration_s: float) -> float:
    """Cost in INR for indic-transcribe (billed per second at ₹0.10/min)."""
    if duration_s <= 0:
        return 0.0
    return round((duration_s / 60.0) * PRICING_INR["indic-transcribe"]["rate_inr"], 6)


def cost_translate(output_tokens: int) -> float:
    """Cost in INR for indic-translate (₹0.20 per 10,000 output tokens)."""
    if output_tokens <= 0:
        return 0.0
    return round((output_tokens / 10_000.0) * PRICING_INR["indic-translate"]["rate_inr"], 6)


def cost_transliterate(output_tokens: int) -> float:
    """Estimated cost in INR for indic-transliterate."""
    if output_tokens <= 0:
        return 0.0
    return round((output_tokens / 10_000.0) * PRICING_INR["indic-transliterate"]["rate_inr"], 6)


def cost_speak(input_chars: int) -> float:
    """Cost in INR for indic-speak (₹6.00 per 10,000 input characters)."""
    if input_chars <= 0:
        return 0.0
    return round((input_chars / 10_000.0) * PRICING_INR["indic-speak"]["rate_inr"], 6)


def cost_ocr(pages: int) -> float:
    """Cost in INR for indic-ocr (₹0.20 per page image)."""
    if pages <= 0:
        return 0.0
    return round(pages * PRICING_INR["indic-ocr"]["rate_inr"], 6)


class DevSpendCapExceeded(RuntimeError):
    """Raised when a live API call would exceed the configured dev spend cap."""


@dataclass
class DevSpendLedger:
    """Tracks cumulative live Bodhan API spend on disk to enforce a hard dev cap."""

    path: Path
    cap_inr: float

    @classmethod
    def from_env(cls) -> DevSpendLedger | None:
        ledger_env = os.environ.get("BODHAN_DEV_LEDGER_PATH")
        if not ledger_env:
            return None
        cap_inr = float(os.environ.get("BODHAN_DEV_SPEND_CAP_INR", "50.0"))
        return cls(path=Path(ledger_env), cap_inr=cap_inr)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"total_inr": 0.0, "cap_inr": self.cap_inr, "entries": []}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"total_inr": 0.0, "cap_inr": self.cap_inr, "entries": []}

    def total_inr(self) -> float:
        return float(self._load().get("total_inr", 0.0))

    def check_allowed(self, estimated_cost_inr: float) -> None:
        current = self.total_inr()
        if current + estimated_cost_inr > self.cap_inr + 1e-9:
            raise DevSpendCapExceeded(
                f"Dev spend cap exceeded: current ₹{current:.4f} + est ₹{estimated_cost_inr:.4f} > cap ₹{self.cap_inr:.2f}"
            )

    def record(self, model: str, cost_inr: float, detail: str = "") -> float:
        data = self._load()
        data["cap_inr"] = self.cap_inr
        data["total_inr"] = round(float(data.get("total_inr", 0.0)) + cost_inr, 6)
        data.setdefault("entries", []).append(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": model,
                "cost_inr": round(cost_inr, 6),
                "detail": detail,
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return data["total_inr"]
