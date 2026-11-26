import os

from dotenv import load_dotenv

load_dotenv()


def _normalize_base_url(url: str) -> str:
    cleaned = url.strip().rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    return cleaned or "https://api.bodhan.ai"


class BodhanConfig:
    """System configuration for Bodhan AI Video Dubbing Pipeline."""

    BASE_URL: str = _normalize_base_url(
        os.getenv("BODHAN_BASE_URL") or os.getenv("BODHAN_API_BASE_URL") or "https://api.bodhan.ai"
    )
    API_KEY: str = os.getenv("BODHAN_API_KEY", "")
    TRANSCRIBE_API_KEY: str = os.getenv("BODHAN_TRANSCRIBE_API_KEY") or API_KEY
    TRANSLATE_API_KEY: str = os.getenv("BODHAN_TRANSLATE_API_KEY") or API_KEY
    SPEAK_API_KEY: str = os.getenv("BODHAN_SPEAK_API_KEY") or API_KEY

    DEFAULT_VOICE: str = os.getenv("BODHAN_DEFAULT_VOICE", "Kavya")
    MIN_SPEEDUP: float = float(os.getenv("BODHAN_MIN_SPEEDUP", "0.85"))
    MAX_SPEEDUP: float = float(os.getenv("BODHAN_MAX_SPEEDUP", "1.20"))
    MAX_CONDENSATION_RETRIES: int = int(os.getenv("BODHAN_MAX_CONDENSATION_RETRIES", "2"))
    MAX_WORKERS: int = max(1, int(os.getenv("BODHAN_MAX_WORKERS", "2")))
    TARGET_LUFS: float = float(os.getenv("BODHAN_TARGET_LUFS", "-16.0"))
    TIMELINE_TOLERANCE_MS: float = float(os.getenv("BODHAN_TIMELINE_TOLERANCE_MS", "100.0"))
    SEGMENT_ALIGNMENT_TOLERANCE_MS: float = float(os.getenv("BODHAN_SEGMENT_ALIGNMENT_TOLERANCE_MS", "300.0"))

    # Production-tier supported Indic languages (ISO-639-1 / Bodhan identifiers)
    SUPPORTED_LANGUAGES: dict[str, str] = {
        "hi": "Hindi",
        "ta": "Tamil",
        "bn": "Bengali",
        "te": "Telugu",
        "mr": "Marathi",
        "gu": "Gujarati",
        "kn": "Kannada",
        "ml": "Malayalam",
        "pa": "Punjabi",
        "or": "Odia",
        "as": "Assamese",
        "ur": "Urdu",
        "sa": "Sanskrit",
    }

    # Official default Indic-Speak voices per language (from Bodhan API rules)
    DEFAULT_VOICES: dict[str, str] = {
        "as": "Prastuti",
        "bn": "Ishita",
        "brx": "Gwrbw",
        "doi": "Preeti",
        "en": "Amit",
        "gu": "Dhara",
        "hi": "Kavya",
        "kn": "Deepika",
        "kok": "Anjali",
        "ks": "Zoon",
        "mai": "Vaidehi",
        "ml": "Lakshmi",
        "mni": "Thoibi",
        "mr": "Anagha",
        "ne": "Srijana",
        "or": "Itishree",
        "pa": "Kaur",
        "sa": "Bharati",
        "sat": "Phulmani",
        "sd": "Moomal",
        "ta": "Anitha",
        "te": "Sravani",
        "ur": "Saba",
    }

    @classmethod
    def get_language_name(cls, code: str) -> str:
        return cls.SUPPORTED_LANGUAGES.get(code.lower(), code)

    @classmethod
    def get_default_voice(cls, code: str) -> str:
        return cls.DEFAULT_VOICES.get(code.lower(), cls.DEFAULT_VOICE)

    @classmethod
    def validate_languages(cls, langs: list[str]) -> list[str]:
        cleaned = []
        for lang in langs:
            code = lang.strip().lower()
            if not code:
                continue
            cleaned.append(code)
        return cleaned
