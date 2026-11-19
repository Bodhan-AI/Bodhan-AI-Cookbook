"""Cost tracking and calculation per Bodhan model.

Prices (INR):
- Indic-Transcribe : ₹0.10 / audio minute
- Indic-Translate  : ₹0.20 / 10,000 output tokens
- Indic-Speak      : ₹6.00 / 10,000 input characters
- Indic-OCR        : ₹0.20 / image (or PDF page)
"""
from __future__ import annotations

import math
from bodhan_mcp.config import settings


def calculate_stt_cost(duration_seconds: float) -> float:
    """Calculate STT cost in INR for given duration in seconds (per minute, ceiling)."""
    if duration_seconds <= 0:
        return 0.0
    minutes = math.ceil(duration_seconds / 60.0)
    cost = minutes * settings.stt_price_per_minute
    return round(cost, 6)


def calculate_translate_cost(output_tokens: int) -> float:
    """Calculate translation cost in INR for output tokens."""
    if output_tokens <= 0:
        return 0.0
    cost = (output_tokens / 10_000.0) * settings.translate_price_per_10k_tokens
    return round(cost, 6)


def calculate_tts_cost(char_count: int) -> float:
    """Calculate TTS cost in INR for character count."""
    if char_count <= 0:
        return 0.0
    cost = (char_count / 10_000.0) * settings.tts_price_per_10k_chars
    return round(cost, 6)


def calculate_ocr_cost(num_images: int) -> float:
    """Calculate OCR cost in INR for number of images/pages."""
    if num_images <= 0:
        return 0.0
    cost = num_images * settings.ocr_price_per_image
    return round(cost, 6)


# Aliases for compatibility
estimate_stt_cost = calculate_stt_cost
estimate_translate_cost = calculate_translate_cost
estimate_tts_cost = calculate_tts_cost
estimate_ocr_cost = calculate_ocr_cost
