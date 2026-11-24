from __future__ import annotations

from ..cache import price_table
from ..languages import ALL_SPEAK_VOICES, DEFAULT_VOICES, SUPPORTED_LANGUAGES
from ..models import success

_MODEL_NAMES = {
    "transcribe": "Indic-Transcribe",
    "translate": "Indic-Translate",
    "speak": "Indic-Speak",
    "ocr": "Indic-OCR",
}


def list_capabilities() -> dict:
    prices = price_table()
    models = {
        model: {"name": _MODEL_NAMES[model], "pricing": prices[model]} for model in _MODEL_NAMES
    }
    return success(
        {
            "models": models,
            "pricing": prices,
            "supported_languages": sorted(SUPPORTED_LANGUAGES),
            "default_voices": DEFAULT_VOICES,
            "limits": {
                "max_audio_chunk_seconds": 30.0,
                "translation_chunk_characters": 15_000,
                "tts_chunk_characters": 500,
                "pdf_pages": "All pages are processed unless page_number is supplied.",
                "notes": (
                    f"Configured for {len(ALL_SPEAK_VOICES)} Bodhan speaker voices, "
                    "PNG/JPEG/PDF/TIFF OCR (with table_format support), and audio/video "
                    "segmentation via FFmpeg."
                ),
            },
        }
    )