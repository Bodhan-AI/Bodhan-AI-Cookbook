"""
Pricing configuration and cost calculation for Bodhan AI models.
Rates:
- Indic-Transcribe: ₹0.10 per input audio minute
- Indic-Translate: ₹0.20 per 10,000 output tokens
- Indic-Speak: ₹6.00 per 10,000 input characters
- Indic-OCR: ₹0.20 per image / page
"""

RATE_TRANSCRIBE_PER_MINUTE = 0.10
RATE_TRANSLATE_PER_10K_TOKENS = 0.20
RATE_SPEAK_PER_10K_CHARS = 6.00
RATE_OCR_PER_IMAGE = 0.20


def calculate_transcribe_cost(duration_seconds: float) -> float:
    """Calculate cost in INR for audio transcription based on duration in seconds."""
    if duration_seconds <= 0:
        return 0.0
    minutes = duration_seconds / 60.0
    return round(minutes * RATE_TRANSCRIBE_PER_MINUTE, 6)


def calculate_translate_cost(token_count: int) -> float:
    """Calculate cost in INR for translation based on token count."""
    if token_count <= 0:
        return 0.0
    return round((token_count / 10_000.0) * RATE_TRANSLATE_PER_10K_TOKENS, 6)


def calculate_speak_cost(character_count: int) -> float:
    """Calculate cost in INR for speech synthesis based on character count."""
    if character_count <= 0:
        return 0.0
    return round((character_count / 10_000.0) * RATE_SPEAK_PER_10K_CHARS, 6)


def calculate_ocr_cost(image_count: int) -> float:
    """Calculate cost in INR for OCR based on number of images/pages."""
    if image_count <= 0:
        return 0.0
    return round(image_count * RATE_OCR_PER_IMAGE, 6)
