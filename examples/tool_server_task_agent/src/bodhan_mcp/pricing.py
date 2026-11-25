"""Published Bodhan hosted prices (INR) and cost helpers. Shared by the server and the agent's budget guard."""
from __future__ import annotations

TRANSCRIBE_PER_AUDIO_MIN = 0.10
TRANSLATE_PER_10K_OUTPUT_TOKENS = 0.20
SPEAK_PER_10K_CHARS = 6.00
OCR_PER_IMAGE = 0.20

PRICE_TABLE_TEXT = (
    "Prices (INR): transcribe 0.10 per audio minute; translate 0.20 per 10,000 output tokens (~3 chars/token for Indic output); "
    "speak 6.00 per 10,000 input characters; OCR 0.20 per page image."
)


def transcribe_cost(duration_s: float) -> float:
    return duration_s / 60.0 * TRANSCRIBE_PER_AUDIO_MIN


def translate_cost(output_tokens: int) -> float:
    return output_tokens / 10_000 * TRANSLATE_PER_10K_OUTPUT_TOKENS


def speak_cost(chars: int) -> float:
    return chars / 10_000 * SPEAK_PER_10K_CHARS


def ocr_cost(pages: int = 1) -> float:
    return OCR_PER_IMAGE * pages


def estimate_translate_tokens(text: str) -> int:
    """Conservative output-token estimate before calling (Indic scripts tokenise at roughly 2-3 chars/token)."""
    return max(1, int(len(text) / 2.5))
