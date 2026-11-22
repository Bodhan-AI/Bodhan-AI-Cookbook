"""Price table (INR) and a running cost meter for budget enforcement."""
from __future__ import annotations

STT_INR_PER_MINUTE = 0.10                 # per input audio minute (billed per second)
TRANSLATE_INR_PER_10K_OUTPUT_TOKENS = 0.20
TTS_INR_PER_10K_CHARS = 6.0               # per input character
OCR_INR_PER_IMAGE = 0.20


def stt_cost(seconds: float) -> float:
    return seconds / 60.0 * STT_INR_PER_MINUTE


def translate_cost(output_tokens: int) -> float:
    return output_tokens / 10_000 * TRANSLATE_INR_PER_10K_OUTPUT_TOKENS


def tts_cost(chars: int) -> float:
    return chars / 10_000 * TTS_INR_PER_10K_CHARS


def ocr_cost(images: int) -> float:
    return images * OCR_INR_PER_IMAGE


def estimate_translate_tokens(chars: int) -> int:
    """Rough, conservative placeholder (about 1 token per 2 characters).

    Replace with the usage figure from the API response when it provides one.
    """
    return max(1, chars // 2)


class BudgetExceeded(Exception):
    """Raised when a call would push the run over its cost limit."""


class CostMeter:
    """Tracks spend per call so the agent can stop before exceeding --max-cost-inr."""

    def __init__(self, limit_inr: float | None = None):
        self.limit_inr = limit_inr
        self.entries: list[dict] = []

    @property
    def total(self) -> float:
        return round(sum(e["inr"] for e in self.entries), 6)

    @property
    def remaining(self) -> float | None:
        return None if self.limit_inr is None else round(self.limit_inr - self.total, 6)

    def would_exceed(self, extra_inr: float) -> bool:
        return self.limit_inr is not None and self.total + extra_inr > self.limit_inr + 1e-9

    def check(self, extra_inr: float) -> None:
        """Call BEFORE a paid request; raises BudgetExceeded if it would not fit."""
        if self.would_exceed(extra_inr):
            raise BudgetExceeded(
                f"Next call costs about Rs {extra_inr:.2f} but only Rs {self.remaining:.2f} of the "
                f"Rs {self.limit_inr:.2f} budget is left."
            )

    def add(self, kind: str, inr: float, note: str = "") -> None:
        self.entries.append({"kind": kind, "inr": inr, "note": note})
