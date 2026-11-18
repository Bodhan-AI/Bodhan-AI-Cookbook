"""Configuration module for Bodhan MCP Server & Agent.

Manages environment variables, pricing constants, rate limits,
language definitions, and cost calculation helpers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set

# Attempt loading dotenv if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ------------------------------------------------------------------------------
# Supported Languages Definition (22 Scheduled Indian Languages + English)
# ------------------------------------------------------------------------------
SUPPORTED_LANGUAGES: Dict[str, str] = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "ks": "Kashmiri",
    "kok": "Konkani",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}

# Alias mapping for user/LLM friendliness
LANGUAGE_ALIASES: Dict[str, str] = {
    "assamese": "as",
    "bengali": "bn",
    "bangla": "bn",
    "bodo": "brx",
    "dogri": "doi",
    "english": "en",
    "gujarati": "gu",
    "hindi": "hi",
    "kannada": "kn",
    "kashmiri": "ks",
    "konkani": "kok",
    "maithili": "mai",
    "malayalam": "ml",
    "manipuri": "mni",
    "meitei": "mni",
    "marathi": "mr",
    "nepali": "ne",
    "odia": "or",
    "oriya": "or",
    "punjabi": "pa",
    "panjabi": "pa",
    "sanskrit": "sa",
    "santali": "sat",
    "sindhi": "sd",
    "tamil": "ta",
    "telugu": "te",
    "urdu": "ur",
}


def normalize_language_code(lang: str) -> Optional[str]:
    """Normalize input language name or code to standard ISO-639 code.
    
    Returns normalized 2/3 letter code if supported, else None.
    """
    if not lang:
        return None
    cleaned = lang.strip().lower()
    if cleaned in SUPPORTED_LANGUAGES:
        return cleaned
    if cleaned in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[cleaned]
    return None


def is_supported_language(lang: str) -> bool:
    """Check if a language is supported by Bodhan Indic models."""
    return normalize_language_code(lang) is not None


# ------------------------------------------------------------------------------
# Bodhan Configuration Class
# ------------------------------------------------------------------------------
@dataclass
class BodhanConfig:
    """Central configuration for Bodhan services and limits."""

    # Bodhan Endpoints and Keys
    base_url: str = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")
    api_key: str = os.getenv("BODHAN_API_KEY", "")

    transcribe_key: str = (
        os.getenv("BODHAN_TRANSCRIBE_KEY") or os.getenv("BODHAN_API_KEY", "")
    )
    translate_key: str = (
        os.getenv("BODHAN_TRANSLATE_KEY") or os.getenv("BODHAN_API_KEY", "")
    )
    speak_key: str = os.getenv("BODHAN_SPEAK_KEY") or os.getenv("BODHAN_API_KEY", "")
    ocr_key: str = os.getenv("BODHAN_OCR_KEY") or os.getenv("BODHAN_API_KEY", "")

    # LLM Settings (for Agent planning only, never for Indian language generation)
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")

    # Pricing (in Indian Rupees - INR)
    price_transcribe_per_min_inr: float = float(
        os.getenv("BODHAN_PRICE_TRANSCRIBE_PER_MIN_INR", "0.10")
    )
    price_translate_per_10k_tokens_inr: float = float(
        os.getenv("BODHAN_PRICE_TRANSLATE_PER_10K_TOKENS_INR", "0.20")
    )
    price_speak_per_10k_chars_inr: float = float(
        os.getenv("BODHAN_PRICE_SPEAK_PER_10K_CHARS_INR", "6.00")
    )
    price_ocr_per_page_inr: float = float(
        os.getenv("BODHAN_PRICE_OCR_PER_PAGE_INR", "0.20")
    )

    # Operational Limits
    max_audio_chunk_duration_sec: float = 30.0  # Bodhan max audio window
    max_speak_chunk_chars: int = 500            # Short-input window for high quality TTS
    max_translate_chunk_chars: int = 4000       # Preserves context without exceeding token chunks
    request_timeout_sec: float = float(os.getenv("BODHAN_REQUEST_TIMEOUT_SEC", "60.0"))
    max_retries: int = int(os.getenv("BODHAN_MAX_RETRIES", "3"))
    max_concurrency: int = int(os.getenv("BODHAN_MAX_CONCURRENCY", "5"))

    # Caching
    cache_enabled: bool = os.getenv("BODHAN_CACHE_ENABLED", "true").lower() in ("true", "1", "yes")
    cache_dir: Path = Path(os.getenv("BODHAN_CACHE_DIR", ".cache/bodhan"))

    def get_api_key_for(self, service: str) -> str:
        """Get the specific API key for a service with fallback to universal key."""
        service = service.lower()
        if service == "transcribe":
            return self.transcribe_key or self.api_key
        elif service == "translate":
            return self.translate_key or self.api_key
        elif service == "speak":
            return self.speak_key or self.api_key
        elif service == "ocr":
            return self.ocr_key or self.api_key
        return self.api_key

    def calculate_transcribe_cost(self, duration_seconds: float) -> float:
        """Calculate transcription cost in INR based on audio duration."""
        minutes = max(0.0, duration_seconds) / 60.0
        return round(minutes * self.price_transcribe_per_min_inr, 6)

    def calculate_translate_cost(self, token_or_word_count: int) -> float:
        """Calculate translation cost in INR based on token count."""
        # Cost per 10k tokens
        return round(
            (max(0, token_or_word_count) / 10000.0) * self.price_translate_per_10k_tokens_inr,
            6,
        )

    def calculate_speak_cost(self, char_count: int) -> float:
        """Calculate speech synthesis cost in INR based on character count."""
        # ₹6.00 per 10k characters
        return round(
            (max(0, char_count) / 10000.0) * self.price_speak_per_10k_chars_inr,
            6,
        )

    def calculate_ocr_cost(self, page_count: int) -> float:
        """Calculate OCR cost in INR based on page / image count."""
        return round(max(0, page_count) * self.price_ocr_per_page_inr, 6)


# Global default configuration instance
config = BodhanConfig()
