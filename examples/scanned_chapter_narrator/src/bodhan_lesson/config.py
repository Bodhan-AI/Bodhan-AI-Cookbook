"""Configuration and rate cards for Bodhan Lesson pipeline."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Automatically load .env if present
load_dotenv()
_recipe_env = Path(__file__).resolve().parents[2] / ".env"
if _recipe_env.is_file():
    load_dotenv(_recipe_env)
_repo_env = Path(__file__).resolve().parents[4] / ".env"
if _repo_env.is_file():
    load_dotenv(_repo_env)

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")

# Bodhan Model Keys
BODHAN_API_KEY = os.environ.get("BODHAN_API_KEY", "")
OCR_KEY = os.environ.get("BODHAN_OCR_KEY") or BODHAN_API_KEY
TRANSLATE_KEY = os.environ.get("BODHAN_TRANSLATE_KEY") or BODHAN_API_KEY
SPEAK_KEY = os.environ.get("BODHAN_SPEAK_KEY") or BODHAN_API_KEY
TRANSCRIBE_KEY = os.environ.get("BODHAN_TRANSCRIBE_KEY") or BODHAN_API_KEY

# General-purpose LLM for English pedagogical narration scripting
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "openrouter/free")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

# Rate cards as officially specified in assignment rules
PRICE_OCR_PER_IMAGE = 0.20          # ₹0.20 per image
PRICE_TRANSLATE_PER_10K_TOKENS = 0.20  # ₹0.20 per 10k output tokens
PRICE_SPEAK_PER_10K_CHARS = 6.00    # ₹6.00 per 10k input characters
PRICE_TRANSCRIBE_PER_MINUTE = 0.10  # ₹0.10 per input audio minute

# Official production voice mappings from bodhan_api_rules.json
DEFAULT_VOICES: dict[str, dict[str, str]] = {
    "hi": {"female": "Kavya", "male": "Amit"},
    "ta": {"female": "Anitha", "male": "Arun"},
    "bn": {"female": "Ishita", "male": "Sourav"},
    "te": {"female": "Sravani", "male": "Vamsi"},
    "mr": {"female": "Anagha", "male": "Chinmay"},
    "gu": {"female": "Dhara", "male": "Parth"},
    "kn": {"female": "Deepika", "male": "Adarsh"},
    "ml": {"female": "Lakshmi", "male": "Kiran"},
    "pa": {"female": "Kaur", "male": "Manpreet"},
    "or": {"female": "Itishree", "male": "Akash"},
    "as": {"female": "Prastuti", "male": "Ankur"},
    "ur": {"female": "Saba", "male": "Zaid"},
    "sa": {"female": "Bharati", "male": "Aryaman"},
}

# 13 production-tier Indic languages supported by Bodhan models + English
SUPPORTED_LANGUAGES: set[str] = set(DEFAULT_VOICES.keys()) | {"en"}

# Mock Mode dynamic checker
def is_mock_mode() -> bool:
    env_mock = os.environ.get("BODHAN_MOCK", "").lower() in ("1", "true", "yes")
    keys_missing = not (OCR_KEY or TRANSLATE_KEY or SPEAK_KEY or TRANSCRIBE_KEY)
    is_placeholder = any(
        "placeholder" in k.lower() or "your_" in k.lower() or "live_key" in k.lower()
        for k in [OCR_KEY, TRANSLATE_KEY, SPEAK_KEY, TRANSCRIBE_KEY]
        if k
    )
    return env_mock or keys_missing or is_placeholder


MOCK_MODE = is_mock_mode()


# Persistent cache directory
CACHE_DIR = Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
