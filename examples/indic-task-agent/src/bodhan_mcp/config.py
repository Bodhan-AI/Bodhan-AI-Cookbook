"""
Configuration and constants for Bodhan MCP Server and Task Agent.
Defines pricing, model endpoints, limits, and supported languages.
"""

import os
from pathlib import Path

# Base directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = PROJECT_ROOT / ".cache" / "bodhan"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Auto-load .env from PROJECT_ROOT if it exists
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    with open(_env_file, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _k, _v = _k.strip(), _v.strip()
                if _k and _k not in os.environ:
                    os.environ[_k] = _v.strip("'\"")

# ---------------------------------------------------------------------------
# API Endpoints & Secrets
# ---------------------------------------------------------------------------
# Defaults to hosted API (for reviewers), can be overridden in .env for local cluster
BODHAN_BASE_URL = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")

# Per-model API keys (required for hosted API, optional for local cluster)
BODHAN_TRANSCRIBE_KEY = os.getenv("BODHAN_TRANSCRIBE_KEY", "")
BODHAN_TRANSLATE_KEY = os.getenv("BODHAN_TRANSLATE_KEY", "")
BODHAN_SPEAK_KEY = os.getenv("BODHAN_SPEAK_KEY", "")
BODHAN_OCR_KEY = os.getenv("BODHAN_OCR_KEY", "")

# LLM Orchestrator Configuration (OpenAI-compatible)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8005/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
LLM_API_KEY = os.getenv("LLM_API_KEY", "dummy")

# ---------------------------------------------------------------------------
# Pricing in INR (Verbatim from Take-Home Specification)
# ---------------------------------------------------------------------------
PRICING_INR = {
    # ₹0.10 per input audio minute
    "transcribe_per_minute": 0.10,
    # ₹0.20 per 10,000 output tokens
    "translate_per_10k_tokens": 0.20,
    # ₹6.00 per 10,000 input characters
    "speak_per_10k_chars": 6.00,
    # ₹0.20 per image/page
    "ocr_per_image": 0.20,
}

# ---------------------------------------------------------------------------
# Model Hardware & Window Limits
# ---------------------------------------------------------------------------
# Indic-Transcribe processes audio in windows of up to 30 seconds
TRANSCRIBE_MAX_WINDOW_SEC = 30.0

# Indic-Speak recommends short, natural sentence inputs
TTS_MAX_CHUNK_CHARS = 400

# Standard Loudness Target for synthesized speech
TARGET_LOUDNESS_LUFS = -16.0

# Agent Default Execution Limits
DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_COST_INR = 20.0
DEFAULT_TIMEOUT_SEC = 120.0

# ---------------------------------------------------------------------------
# Supported Indian Languages (ISO 639-1 / 639-3 codes)
# ---------------------------------------------------------------------------
# 22 Constitutionally Scheduled Indian Languages + English
SUPPORTED_INDIC_LANGUAGES = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "gom": "Konkani",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "ks": "Kashmiri",
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
    "en": "English",
}

# ---------------------------------------------------------------------------
# Indic-Speak Production Voices (The Official 44-Voice Rasa Studio Roster)
# Extracted directly from Bodhan's official model specification (voices.md)
# ---------------------------------------------------------------------------
INDIC_SPEAK_OFFICIAL_VOICES = {
    "as": {"female": "Prastuti", "male": "Ankur"},
    "bn": {"female": "Ishita", "male": "Sourav"},
    "brx": {"female": "Gwrbw", "male": "Sansuma"},
    "doi": {"female": "Preeti", "male": "Sham"},
    "gu": {"female": "Dhara", "male": "Parth"},
    "hi": {"female": "Kavya", "male": "Amit"},
    "kn": {"female": "Deepika", "male": "Adarsh"},
    "ks": {"female": "Zoon", "male": "Ishfaq"},
    "gom": {"female": "Anjali", "male": "Sandeep"},
    "mai": {"female": "Vaidehi", "male": "Madhukar"},
    "ml": {"female": "Lakshmi", "male": "Kiran"},
    "mni": {"female": "Thoibi", "male": "Chaoba"},
    "mr": {"female": "Anagha", "male": "Chinmay"},
    "ne": {"female": "Srijana", "male": "Sagar"},
    "or": {"female": "Itishree", "male": "Akash"},
    "pa": {"female": "Kaur", "male": "Manpreet"},
    "sa": {"female": "Bharati", "male": "Aryaman"},
    "sat": {"female": "Phulmani", "male": "Sibu"},
    "sd": {"female": "Moomal", "male": "Rano"},
    "ta": {"female": "Anitha", "male": "Arun"},
    "te": {"female": "Sravani", "male": "Vamsi"},
    "ur": {"female": "Saba", "male": "Zaid"},
    "en": {"female": "Kavya", "male": "Amit"},
}

# List format [male, female] for backward compatibility
INDIC_SPEAK_SAMPLE_VOICES = {
    code: [v["male"], v["female"]]
    for code, v in INDIC_SPEAK_OFFICIAL_VOICES.items()
}

def get_voice(lang: str, gender: str = "female") -> str:
    """Returns the official Bodhan voice name for a given language and gender."""
    norm = normalize_lang_code(lang)
    pair = INDIC_SPEAK_OFFICIAL_VOICES.get(norm, {"female": "Kavya", "male": "Amit"})
    return pair.get(gender.lower(), pair["female"])

def is_language_supported(lang_code: str) -> bool:
    """Returns True if the language is supported by Bodhan models."""
    if not lang_code:
        return False
    code = lang_code.strip().lower()
    return code in SUPPORTED_INDIC_LANGUAGES

def normalize_lang_code(lang_str: str) -> str:
    """Normalizes language name or code (e.g. 'Hindi' -> 'hi', 'TAMIL' -> 'ta')."""
    if not lang_str:
        return ""
    clean = lang_str.strip().lower()
    if clean in SUPPORTED_INDIC_LANGUAGES:
        return clean
    for code, name in SUPPORTED_INDIC_LANGUAGES.items():
        if name.lower() == clean:
            return code
    return clean
