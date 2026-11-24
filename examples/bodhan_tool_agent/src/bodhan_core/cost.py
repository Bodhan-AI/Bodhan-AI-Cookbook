"""Cost estimation helpers and CostMeter for tracking API spend."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Pricing constants
# ---------------------------------------------------------------------------

TRANSCRIBE_PER_MINUTE: float = 0.10        # INR per audio minute
TRANSLATE_PER_10K_TOKENS: float = 0.20     # INR per 10,000 output tokens
SPEAK_PER_10K_CHARS: float = 6.0           # INR per 10,000 input characters
OCR_PER_IMAGE: float = 0.20               # INR per image/page


# ---------------------------------------------------------------------------
# Estimation functions
# ---------------------------------------------------------------------------

def estimate_transcribe(duration_seconds: float) -> float:
    """Return estimated cost in INR for transcribing *duration_seconds* of audio."""
    minutes = duration_seconds / 60.0
    return minutes * TRANSCRIBE_PER_MINUTE


def estimate_translate(output_tokens: int) -> float:
    """Return estimated cost in INR for translating *output_tokens* tokens."""
    return (output_tokens / 10_000.0) * TRANSLATE_PER_10K_TOKENS


def estimate_speak(input_chars: int) -> float:
    """Return estimated cost in INR for synthesising *input_chars* characters."""
    return (input_chars / 10_000.0) * SPEAK_PER_10K_CHARS


def estimate_ocr(num_pages: int) -> float:
    """Return estimated cost in INR for OCR-ing *num_pages* pages/images."""
    return num_pages * OCR_PER_IMAGE


# ---------------------------------------------------------------------------
# CostMeter
# ---------------------------------------------------------------------------

@dataclass
class CostMeter:
    """Tracks cumulative spend across tool calls within a session."""

    total_inr: float = 0.0
    per_call: list[dict[str, Any]] = field(default_factory=list)

    def add(self, tool: str, cost: float, cached: bool = False) -> None:
        """Record a completed tool call.

        Args:
            tool: Tool name (e.g. ``"transcribe"``).
            cost: Actual cost in INR (0.0 for cached calls).
            cached: Whether the result came from cache.
        """
        actual_cost = 0.0 if cached else cost
        self.total_inr += actual_cost
        self.per_call.append(
            {
                "tool": tool,
                "cost_inr": actual_cost,
                "cached": cached,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        )

    def check_budget(self, estimated: float, remaining: float) -> bool:
        """Return True if proceeding with *estimated* cost stays within *remaining* budget.

        Args:
            estimated: Estimated cost of the next call in INR.
            remaining: Budget remaining in INR (``max_budget - total_inr``).
        """
        return estimated <= remaining

    def summary(self) -> dict[str, Any]:
        """Return a serialisable summary of all calls."""
        return {
            "total_inr": round(self.total_inr, 6),
            "call_count": len(self.per_call),
            "cached_calls": sum(1 for c in self.per_call if c["cached"]),
            "per_call": self.per_call,
        }
