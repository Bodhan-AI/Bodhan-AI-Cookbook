"""Pricing calculator for Bodhan API models in Indian Rupees (INR).

Official Hosted Rates:
- Indic-Transcribe: ₹0.10 per input audio minute (pro-rated per second)
- Indic-Translate:  ₹0.20 per 10,000 output tokens (₹0.00002 / token)
- Indic-Speak:      ₹6.00 per 10,000 input characters (₹0.0006 / char)
- Indic-OCR:        ₹0.20 per image/page
"""

from __future__ import annotations

import math

TRANSCRIBE_INR_PER_MINUTE = 0.10
TRANSLATE_INR_PER_10K_TOKENS = 0.20
SPEAK_INR_PER_10K_CHARS = 6.00
OCR_INR_PER_PAGE = 0.20


def calculate_transcribe_cost(duration_seconds: float) -> float:
    """Calculate transcription cost based on audio duration."""
    minutes = max(0.0, duration_seconds) / 60.0
    return round(minutes * TRANSCRIBE_INR_PER_MINUTE, 4)


def calculate_translate_cost(output_tokens: int) -> float:
    """Calculate translation cost based on output token count."""
    tokens = max(0, output_tokens)
    return round((tokens / 10000.0) * TRANSLATE_INR_PER_10K_TOKENS, 4)


def estimate_tokens_from_text(text: str) -> int:
    """Heuristic estimate of Indic/English tokens for cost calculation when API token count is absent."""
    if not text:
        return 0
    # For Indic scripts, Unicode characters typically compress to ~0.5 to 1.5 tokens per word;
    # approx 1 word = 1.3 tokens.
    words = len(text.split())
    return max(1, math.ceil(words * 1.35))


def calculate_speak_cost(character_count: int) -> float:
    """Calculate speech synthesis cost based on input characters."""
    chars = max(0, character_count)
    return round((chars / 10000.0) * SPEAK_INR_PER_10K_CHARS, 4)


def calculate_ocr_cost(page_count: int) -> float:
    """Calculate OCR cost based on number of processed pages/images."""
    pages = max(1, page_count)
    return round(pages * OCR_INR_PER_PAGE, 4)
