"""Cost model and per-run spend ledger, in rupees.

Hosted prices (assignment brief):
    indic-transcribe  ₹0.10 per input audio minute
    indic-translate   ₹0.20 per 10,000 output tokens (input tokens free)
    indic-speak       ₹6.00 per 10,000 input characters
    indic-ocr         ₹0.20 per image/page
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .errors import ErrorCode, ToolError

INR_PER_AUDIO_MINUTE = 0.10
INR_PER_10K_OUTPUT_TOKENS = 0.20
INR_PER_10K_TTS_CHARS = 6.00
INR_PER_OCR_IMAGE = 0.20

# No tokenizer is published, so output tokens are estimated conservatively from characters
# when the API response carries no usage figure. Indic scripts tokenise densely.
CHARS_PER_TOKEN_ESTIMATE = 2.0


def cost_transcribe(seconds: float) -> float:
    return max(seconds, 0.0) / 60.0 * INR_PER_AUDIO_MINUTE


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def cost_translate(output_tokens: int) -> float:
    return max(output_tokens, 0) / 10_000 * INR_PER_10K_OUTPUT_TOKENS


def cost_speak(chars: int) -> float:
    return max(chars, 0) / 10_000 * INR_PER_10K_TTS_CHARS


def cost_ocr(images: int = 1) -> float:
    return max(images, 0) * INR_PER_OCR_IMAGE


@dataclass
class LedgerEntry:
    model: str
    cost_inr: float
    cached: bool
    detail: dict


@dataclass
class Ledger:
    """Tracks spend for one run. ``limit_inr=None`` means unlimited (e.g. the standalone MCP server)."""

    limit_inr: float | None = None
    entries: list[LedgerEntry] = field(default_factory=list)

    @property
    def spent_inr(self) -> float:
        return sum(e.cost_inr for e in self.entries)

    @property
    def remaining_inr(self) -> float | None:
        return None if self.limit_inr is None else max(self.limit_inr - self.spent_inr, 0.0)

    def check(self, model: str, estimate_inr: float) -> None:
        """Refuse a call *before* it is made if its estimated cost would exceed the budget."""
        if self.limit_inr is None:
            return
        if self.spent_inr + estimate_inr > self.limit_inr + 1e-9:
            raise ToolError(
                ErrorCode.BUDGET_EXCEEDED,
                f"{model} call estimated at ₹{estimate_inr:.4f} would exceed the budget "
                f"(spent ₹{self.spent_inr:.4f} of ₹{self.limit_inr:.2f}).",
                hint="Stop and report partial results, or rerun with a higher --max-cost-inr.",
            )

    def record(self, model: str, cost_inr: float, cached: bool, **detail) -> None:
        self.entries.append(LedgerEntry(model, 0.0 if cached else cost_inr, cached, detail))

    def by_model(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for e in self.entries:
            out[e.model] = out.get(e.model, 0.0) + e.cost_inr
        return out
