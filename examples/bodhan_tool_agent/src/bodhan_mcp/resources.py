"""MCP Resources exposing Bodhan metadata, languages, voices, styles, and pricing."""

from __future__ import annotations

import json
from bodhan_mcp.config import (
    LANGUAGE_NAMES,
    PRICING,
    SPEAK_LANGUAGES,
    SPEAK_STYLES,
    SPEAK_VOICES,
    TRANSCRIBE_LANGUAGES,
    TRANSLATE_LANGUAGES,
)


def get_languages_resource() -> str:
    """Return JSON string of all supported languages and their model capabilities."""
    data = {
        "all_languages": LANGUAGE_NAMES,
        "capabilities": {
            "transcribe": sorted(TRANSCRIBE_LANGUAGES),
            "translate": sorted(TRANSLATE_LANGUAGES),
            "speak": sorted(SPEAK_LANGUAGES),
            "ocr": "All Indic scripts and English (script-agnostic)",
        },
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def get_voices_resource() -> str:
    """Return JSON string of available voices mapped by language and gender."""
    return json.dumps(SPEAK_VOICES, indent=2, ensure_ascii=False)


def get_styles_resource() -> str:
    """Return JSON string of supported text-to-speech speaking styles."""
    return json.dumps(SPEAK_STYLES, indent=2, ensure_ascii=False)


def get_pricing_resource() -> str:
    """Return JSON string of the official pricing schedule in INR."""
    data = {
        "currency": "INR",
        "models": {
            "indic-transcribe": {
                "rate": PRICING.transcribe_per_second_inr,
                "unit": "per second of audio",
                "notes": "Chunks up to ~30 seconds per request",
            },
            "indic-translate": {
                "rate": PRICING.translate_per_1k_out_tokens_inr,
                "unit": "per 1,000 output tokens",
                "notes": "Input tokens are free; output tokens are billed",
            },
            "indic-speak": {
                "rate": PRICING.speak_per_second_inr,
                "unit": "per second of synthesized speech",
                "notes": "24 kHz PCM16 WAV output",
            },
            "indic-ocr": {
                "rate": PRICING.ocr_per_page_inr,
                "unit": "per document page",
                "notes": "Supports PNG, JPG, JPEG, and multi-page PDFs",
            },
        },
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
