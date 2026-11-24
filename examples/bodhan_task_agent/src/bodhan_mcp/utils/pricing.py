from __future__ import annotations

import math

TRANSLATE_INR_PER_10K_OUTPUT_TOKENS = 0.20
SPEAK_INR_PER_10K_INPUT_CHARS = 6.00
TRANSCRIBE_INR_PER_AUDIO_MINUTE = 0.10
OCR_INR_PER_IMAGE = 0.20

RATES = {
    "translate": {"inr": TRANSLATE_INR_PER_10K_OUTPUT_TOKENS, "per": "10,000 output tokens"},
    "speak": {"inr": SPEAK_INR_PER_10K_INPUT_CHARS, "per": "10,000 input characters"},
    "transcribe": {"inr": TRANSCRIBE_INR_PER_AUDIO_MINUTE, "per": "input audio minute"},
    "ocr": {"inr": OCR_INR_PER_IMAGE, "per": "image"},
}


def _check_count(name: str, value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be an integer >= 0")


def _check_duration(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number >= 0")


def translate_cost(completion_tokens: int | None) -> float | None:
    if completion_tokens is None:
        return None
    _check_count("completion_tokens", completion_tokens)
    return round(completion_tokens * TRANSLATE_INR_PER_10K_OUTPUT_TOKENS / 10_000, 6)


def speak_cost(input_chars: int) -> float:
    _check_count("input_chars", input_chars)
    return round(input_chars * SPEAK_INR_PER_10K_INPUT_CHARS / 10_000, 6)


def transcribe_cost(audio_seconds: float) -> float:
    _check_duration("audio_seconds", audio_seconds)
    return round(audio_seconds / 60 * TRANSCRIBE_INR_PER_AUDIO_MINUTE, 6)


def ocr_cost(images: int) -> float:
    _check_count("images", images)
    return round(images * OCR_INR_PER_IMAGE, 6)
