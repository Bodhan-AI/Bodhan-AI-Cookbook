"""Hosted prices and pre-call cost estimates, in rupees.

Estimates are deliberately conservative (they round up) because they gate calls
against a caller's remaining budget: a call is refused before it is sent if its
estimate exceeds ``max_cost_inr``. Actual cost is computed after the call from
what Bodhan bills on (audio seconds, output tokens, input characters, pages).
"""

from __future__ import annotations

from . import languages

TRANSCRIBE_INR_PER_MIN = 0.10
TRANSLATE_INR_PER_10K_OUT_TOKENS = 0.20
SPEAK_INR_PER_10K_CHARS = 6.0
OCR_INR_PER_IMAGE = 0.20


def transcribe_cost(seconds: float) -> float:
    return round(max(seconds, 0.0) / 60.0 * TRANSCRIBE_INR_PER_MIN, 6)


def speak_cost(chars: int) -> float:
    return round(max(chars, 0) / 10_000 * SPEAK_INR_PER_10K_CHARS, 6)


def translate_cost(output_tokens: int) -> float:
    return round(max(output_tokens, 0) / 10_000 * TRANSLATE_INR_PER_10K_OUT_TOKENS, 6)


def ocr_cost(pages: int) -> float:
    return round(max(pages, 0) * OCR_INR_PER_IMAGE, 6)


def estimate_tokens(text: str) -> int:
    """Rough token count. Indic scripts tokenise far denser than English, so count them heavier."""
    if not text:
        return 0
    indic = sum(1 for ch in text if (s := languages.char_script(ch)) and s in languages.INDIC_SCRIPTS)
    other = len(text) - indic
    return int(other / 3.5 + indic / 1.5) + 1


def estimate_translation_output_tokens(text: str, target_lang: str) -> int:
    """Upper-bound guess of output tokens: Indic targets expand relative to English input."""
    base = estimate_tokens(text)
    factor = 1.0 if target_lang == "en" else 2.5
    return int(base * factor) + 16
