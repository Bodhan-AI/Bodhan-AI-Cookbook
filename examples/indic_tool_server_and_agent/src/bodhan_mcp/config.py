"""Configuration: env vars, price table, limits."""
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class Settings:
    # ── Bodhan model API keys ──────────────────────────────────────────────
    transcribe_key: str = os.getenv("LLM_TRANSCRIBE", "")
    translate_key: str = os.getenv("LLM_TRANSLATE", "")
    ocr_key: str = os.getenv("LLM_OCR", "")
    audio_key: str = os.getenv("LLM_AUDIO", "")
    transliterate_key: str = os.getenv("LLM_TRANSLITERATE", "")

    # ── LLM (task-agent reasoning) ─────────────────────────────────────────
    gemini_key: str = os.getenv("GEMINI_API_KEY", "")
    llm_base_url: str = os.getenv(
        "LLM_BASE_URL",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    llm_model: str = os.getenv("LLM_MODEL")
    # Prefer explicit LLM_API_KEY; fall back to GEMINI_API_KEY
    llm_api_key: str = os.getenv("LLM_API_KEY", os.getenv("GEMINI_API_KEY", ""))

    # ── Bodhan hosted API ──────────────────────────────────────────────────
    bodhan_base_url: str = "https://api.bodhan.ai/v1"

    # ── Prices (INR) ──────────────────────────────────────────────────────
    stt_price_per_minute: float = 0.10        # ₹0.10 / audio minute
    translate_price_per_10k_tokens: float = 0.20  # ₹0.20 / 10K output tokens
    tts_price_per_10k_chars: float = 6.0      # ₹6.00 / 10K input chars
    ocr_price_per_image: float = 0.20         # ₹0.20 / image

    # ── Model limits ──────────────────────────────────────────────────────
    stt_max_chunk_seconds: int = 30           # Indic-Transcribe window
    translate_max_tokens: int = 32000         # Indic-Translate context window
    tts_max_chars_per_call: int = 500         # keep each TTS call short

    # ── Reliability ───────────────────────────────────────────────────────
    max_concurrent_api_calls: int = 5
    api_timeout_seconds: int = 120
    max_retry_attempts: int = 3

    # ── Agent defaults ────────────────────────────────────────────────────
    default_max_steps: int = 30
    default_max_cost_inr: float = 20.0

    # ── Supported languages ───────────────────────────────────────────────
    # Indic-Transcribe
    stt_languages: frozenset = frozenset(
        ["hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa",
         "or", "as", "ur", "sa", "mai", "bho", "kok", "ne", "si"]
    )
    # Indic-Translate source/target (en allowed as target only)
    translate_languages: frozenset = frozenset(
        ["hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa",
         "or", "as", "ur", "sa", "mai", "bho", "kok", "ne", "si", "en"]
    )
    # Indic-Speak
    tts_languages: frozenset = frozenset(
        ["hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa"]
    )

    # ── TTS voices per language ───────────────────────────────────────────
    tts_voices: dict = {
        "hi": ["Amit", "Priya"],
        "bn": ["Ravi", "Ananya"],
        "ta": ["Surya", "Devi"],
        "te": ["Arjun", "Kavya"],
        "mr": ["Vikram", "Riya"],
        "gu": ["Kabir", "Nisha"],
        "kn": ["Kiran", "Deepa"],
        "ml": ["Dev", "Meera"],
        "pa": ["Jeet", "Simran"],
    }

    tts_male_voices: dict = {
        "hi": "Amit", "bn": "Ravi", "ta": "Surya", "te": "Arjun",
        "mr": "Vikram", "gu": "Kabir", "kn": "Kiran", "ml": "Dev", "pa": "Jeet"
    }

    tts_female_voices: dict = {
        "hi": "Priya", "bn": "Ananya", "ta": "Devi", "te": "Kavya",
        "mr": "Riya", "gu": "Nisha", "kn": "Deepa", "ml": "Meera", "pa": "Simran"
    }


settings = Settings()
