"""Configuration, model allowlist, endpoints, and pricing for Bodhan AI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load recipe-specific .env first, then fall back to repo-root .env.
# This ensures the correct per-capability keys are always found regardless
# of the working directory from which the module is imported.
_THIS_DIR = Path(__file__).resolve().parent
_RECIPE_ENV = _THIS_DIR.parents[2] / ".env"          # examples/bodhan_tool_agent/.env
_ROOT_ENV = _THIS_DIR.parents[4] / ".env"             # repo root .env
for _env_file in (_RECIPE_ENV, _ROOT_ENV):
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
        break
load_dotenv(override=False)   # finally check CWD .env

BODHAN_BASE_URL = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")
BODHAN_API_KEY = os.getenv("BODHAN_API_KEY", "")

# Task-specific API keys (optional fallback to BODHAN_API_KEY)
BODHAN_TRANSCRIBE_API_KEY = os.getenv("BODHAN_TRANSCRIBE_API_KEY", "") or BODHAN_API_KEY
BODHAN_TRANSLATE_API_KEY = os.getenv("BODHAN_TRANSLATE_API_KEY", "") or BODHAN_API_KEY
BODHAN_TRANSLITERATE_API_KEY = os.getenv("BODHAN_TRANSLITERATE_API_KEY", "") or BODHAN_API_KEY
BODHAN_SPEAK_API_KEY = os.getenv("BODHAN_SPEAK_API_KEY", "") or BODHAN_API_KEY
BODHAN_OCR_API_KEY = os.getenv("BODHAN_OCR_API_KEY", "") or BODHAN_API_KEY

# Exact official model identifiers from scripts/bodhan_api_rules.json
MODEL_TRANSCRIBE = "indic-transcribe"
MODEL_TRANSLATE = "indic-translate"
MODEL_TRANSLITERATE = "indic-transliterate"
MODEL_SPEAK = "indic-speak"
MODEL_OCR = "indic-ocr"

# All official languages supported across Bodhan models
LANGUAGE_NAMES: dict[str, str] = {
    "as": "Assamese",
    "bhb": "Bhili",
    "bho": "Bhojpuri",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri (Perso-Arabic)",
    "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali script)",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}

# Model-specific language capabilities
TRANSCRIBE_LANGUAGES: set[str] = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn",
    "kok", "ks", "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa",
    "sat", "sd", "ta", "te", "ur"
}

TRANSLATE_LANGUAGES: set[str] = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "ks-Deva", "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa",
    "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur"
}

SPEAK_LANGUAGES: set[str] = {
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks",
    "mai", "ml", "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd",
    "ta", "te", "ur"
}

# Voices mapped by language and gender
SPEAK_VOICES: dict[str, dict[str, list[str]]] = {
    "as": {"female": ["Prastuti"], "male": ["Ankur"]},
    "bn": {"female": ["Ishita"], "male": ["Sourav"]},
    "brx": {"female": ["Gwrbw"], "male": ["Sansuma"]},
    "doi": {"female": ["Preeti"], "male": ["Sham"]},
    "gu": {"female": ["Dhara"], "male": ["Parth"]},
    "hi": {"female": ["Kavya", "Suhani"], "male": ["Amit"]},
    "kn": {"female": ["Deepika"], "male": ["Adarsh"]},
    "kok": {"female": ["Anjali"], "male": ["Sandeep"]},
    "ks": {"female": ["Zoon"], "male": ["Ishfaq"]},
    "mai": {"female": ["Vaidehi"], "male": ["Madhukar"]},
    "ml": {"female": ["Lakshmi"], "male": ["Kiran"]},
    "mni": {"female": ["Thoibi"], "male": ["Chaoba"]},
    "mr": {"female": ["Anagha"], "male": ["Chinmay"]},
    "ne": {"female": ["Srijana"], "male": ["Sagar"]},
    "or": {"female": ["Itishree"], "male": ["Akash"]},
    "pa": {"female": ["Kaur"], "male": ["Manpreet"]},
    "sa": {"female": ["Bharati"], "male": ["Aryaman"]},
    "sat": {"female": ["Phulmani"], "male": ["Sibu"]},
    "sd": {"female": ["Moomal"], "male": ["Rano"]},
    "ta": {"female": ["Anitha"], "male": ["Arun"]},
    "te": {"female": ["Sravani"], "male": ["Vamsi"]},
    "ur": {"female": ["Saba"], "male": ["Zaid"]},
}

# English uses Hindi voices by default
SPEAK_VOICES["en"] = {"female": ["Kavya", "Suhani"], "male": ["Amit"]}

SPEAK_STYLES: list[str] = [
    "AIR style news",
    "Customer Care",
    "TV style news",
    "advertisements",
    "anger",
    "children's stories",
    "disgust",
    "educational lecture",
    "fear",
    "happy",
    "news",
    "sad",
    "single person narration audiobook",
    "surprise",
]

TRANSLATE_SCRIPTS: list[str] = ["native", "roman", "codemix"]

# Pricing schedule in INR (Indian Rupees)
@dataclass(frozen=True)
class PricingSchedule:
    transcribe_per_second_inr: float = 0.01      # ~₹0.60/min
    translate_per_1k_out_tokens_inr: float = 0.15 # Input is free, output billed
    speak_per_second_inr: float = 0.03          # ~₹1.80/min of speech (~₹0.005/char)
    ocr_per_page_inr: float = 0.30              # Flat ₹0.30 per page

PRICING = PricingSchedule()

# Operational limits
MAX_AUDIO_CHUNK_SECONDS = 28.0
MAX_TEXT_CHUNK_CHARS = 1800
MAX_TTS_SENTENCE_CHARS = 250
DEFAULT_TIMEOUT_SECONDS = 60.0
OCR_TIMEOUT_SECONDS = 180.0
