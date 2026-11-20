"""Configuration, prices, limits, and supported language sets."""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Upstream API & LLM credentials ---
BODHAN_TRANSCRIBE_KEY = os.getenv("BODHAN_TRANSCRIBE_KEY", "")
BODHAN_TRANSLATE_KEY = os.getenv("BODHAN_TRANSLATE_KEY", "")
BODHAN_SPEAK_KEY = os.getenv("BODHAN_SPEAK_KEY", "")
BODHAN_OCR_KEY = os.getenv("BODHAN_OCR_KEY", "")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_KEY_SECONDARY = os.getenv("LLM_API_KEY_SECONDARY", "")

BODHAN_BASE = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# --- Pricing (INR) per SPEC §6.1 ---
PRICES = {
    "transcribe_per_audio_min": 0.10,
    "translate_per_10k_output_tokens": 0.20,
    "speak_per_10k_chars": 6.00,
    "ocr_per_image": 0.20,
}

# --- Operational limits & chunking defaults per SPEC §6.1 ---
AUDIO_CHUNK_TARGET_S = 25.0
AUDIO_CHUNK_HARD_S = 30.0
SILENCE_MIN_MS = 250
TRANSLATE_CONTEXT_TOK = 32_000
SPEAK_MAX_CHARS = 5_000

# --- Core Indic languages ---
INDIC_LANGS = [
    "hi", "ta", "te", "bn", "mr", "kn", "ml", "gu", "pa", "or", "as", "ur"
]

# --- Supported languages per model (Standing Rule F6) ---
SUPPORTED_LANGS = {
    "transcribe": list(INDIC_LANGS) + ["en"],  # en supported best-effort
    "translate": list(INDIC_LANGS) + ["en"],   # en supported
    "ocr": list(INDIC_LANGS) + ["en"],         # en supported
    "speak": list(INDIC_LANGS),                # en strictly excluded
}

# --- Voice library per language for speech synthesis ---
VOICE_LIBRARY: dict[str, list[str]] = {
    lang: ["Kavya", "Ankur", f"{lang}_female_1", f"{lang}_male_1"]
    for lang in INDIC_LANGS
}
VOICE_LIBRARY["hi"].extend(["hi_female_2", "hi_male_2"])
