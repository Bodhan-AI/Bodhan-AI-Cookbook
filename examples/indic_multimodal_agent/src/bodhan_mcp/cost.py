"""Cost accounting and pre-flight estimation for Bodhan AI models.

Pricing from SPEC.md:
- Indic-Transcribe: ₹0.10 per audio minute
- Indic-Translate: ₹0.20 per 10,000 output tokens
- Indic-Speak: ₹6.00 per 10,000 input characters
- Indic-OCR: ₹0.20 per image / page
"""

from typing import Optional


class Pricing:
    TRANSCRIBE_PER_MIN_INR = 0.10
    TRANSLATE_PER_10K_TOKENS_INR = 0.20
    SPEAK_PER_10K_CHARS_INR = 6.00
    OCR_PER_IMAGE_INR = 0.20


def calculate_transcribe_cost(duration_seconds: float) -> float:
    """Calculate cost of transcription based on duration in seconds."""
    minutes = max(0.0, duration_seconds) / 60.0
    return round(minutes * Pricing.TRANSCRIBE_PER_MIN_INR, 4)


def calculate_translate_cost(output_tokens: int) -> float:
    """Calculate cost of translation based on output token count."""
    return round((max(0, output_tokens) / 10000.0) * Pricing.TRANSLATE_PER_10K_TOKENS_INR, 4)


def calculate_speak_cost(character_count: int) -> float:
    """Calculate cost of speech synthesis based on input character count."""
    return round((max(0, character_count) / 10000.0) * Pricing.SPEAK_PER_10K_CHARS_INR, 4)


def calculate_ocr_cost(page_count: int) -> float:
    """Calculate cost of OCR based on page or image count."""
    return round(max(0, page_count) * Pricing.OCR_PER_IMAGE_INR, 4)


def estimate_preflight_cost(
    audio_duration_s: float = 0.0,
    estimated_translate_words: int = 0,
    tts_characters: int = 0,
    ocr_pages: int = 0,
) -> float:
    """Pre-flight cost estimate across planned task operations.
    Translates words to tokens roughly using 1 word ≈ 1.5 tokens.
    """
    cost = 0.0
    if audio_duration_s > 0:
        cost += calculate_transcribe_cost(audio_duration_s)
    if estimated_translate_words > 0:
        tokens = int(estimated_translate_words * 1.5)
        cost += calculate_translate_cost(tokens)
    if tts_characters > 0:
        cost += calculate_speak_cost(tts_characters)
    if ocr_pages > 0:
        cost += calculate_ocr_cost(ocr_pages)
    return round(cost, 4)

