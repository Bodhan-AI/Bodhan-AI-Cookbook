"""Cost accounting and budget enforcement for one tool call.

A `Ledger` is created per tool call. Before every *billable* request the client calls
`reserve(estimate)`, which raises `budget_exceeded` if the call could push spending past
`max_cost_inr`. Cache hits cost nothing and skip the check. After the request, the real
cost is recorded with `add()`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .catalog import PRICING
from .errors import BodhanToolError


def transcribe_cost(seconds: float) -> float:
    return max(seconds, 0.0) / 60.0 * PRICING["indic-transcribe"]["per_audio_minute"]


def translate_cost(output_tokens: int) -> float:
    return max(output_tokens, 0) / 10_000 * PRICING["indic-translate"]["per_10k_output_tokens"]


def estimate_translate_tokens(text: str) -> int:
    # Indic scripts tokenise to roughly one token per 1-3 characters; assume the expensive end.
    return math.ceil(len(text) / 1.5) + 32


def speak_cost(chars: int) -> float:
    return max(chars, 0) / 10_000 * PRICING["indic-speak"]["per_10k_input_chars"]


def ocr_cost(images: int) -> float:
    return max(images, 0) * PRICING["indic-ocr"]["per_image"]


@dataclass
class CallRecord:
    model: str
    cost_inr: float
    cached: bool
    latency_ms: int
    attempts: int


class ModelUsage(BaseModel):
    calls: int = Field(0, description="Requests made to the model (excluding cache hits).")
    cached: int = Field(0, description="Requests answered from the local cache at no cost.")
    cost_inr: float = 0.0


class Usage(BaseModel):
    cost_inr: float = Field(description="Total rupees spent by this tool call.")
    by_model: dict[str, ModelUsage] = Field(default_factory=dict)


@dataclass
class Ledger:
    max_cost_inr: float | None = None
    spent: float = 0.0
    records: list[CallRecord] = field(default_factory=list)

    def remaining(self) -> float | None:
        return None if self.max_cost_inr is None else self.max_cost_inr - self.spent

    def reserve(self, estimate: float, what: str) -> None:
        if self.max_cost_inr is None:
            return
        if self.spent + estimate > self.max_cost_inr + 1e-9:
            raise BodhanToolError(
                "budget_exceeded",
                f"{what} would cost about ₹{estimate:.4f}, but only ₹{max(self.max_cost_inr - self.spent, 0):.4f} of max_cost_inr is left.",
                hint="Raise max_cost_inr, shorten the input, or stop and report what was done.",
                details={
                    "estimate_inr": round(estimate, 4),
                    "spent_inr": round(self.spent, 4),
                    "max_cost_inr": self.max_cost_inr,
                },
            )

    def add(self, record: CallRecord) -> None:
        self.records.append(record)
        self.spent += record.cost_inr

    def usage(self) -> Usage:
        by: dict[str, ModelUsage] = {}
        for r in self.records:
            u = by.setdefault(r.model, ModelUsage())
            if r.cached:
                u.cached += 1
            else:
                u.calls += 1
            u.cost_inr = round(u.cost_inr + r.cost_inr, 6)
        return Usage(cost_inr=round(self.spent, 6), by_model=by)
