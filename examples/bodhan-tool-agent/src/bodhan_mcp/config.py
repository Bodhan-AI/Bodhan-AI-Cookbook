"""Configuration management for Bodhan tool server and agent.

Reads and validates the 7 required environment variables without logging secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import dotenv


@dataclass(frozen=True)
class Settings:
    bodhan_transcribe_key: str
    bodhan_translate_key: str
    bodhan_speak_key: str
    bodhan_ocr_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    bodhan_base_url: str = "https://api.bodhan.ai"

    def get_model_key(self, model_id: str) -> str:
        """Returns the specific Bodhan API key for a given model ID."""
        mapping = {
            "indic-transcribe": self.bodhan_transcribe_key,
            "indic-translate": self.bodhan_translate_key,
            "indic-speak": self.bodhan_speak_key,
            "indic-ocr": self.bodhan_ocr_key,
        }
        key = mapping.get(model_id, "")
        if not key:
            raise ValueError(f"No API key configured for model {model_id!r}")
        return key


def load_settings(env_file: Path | str | None = None) -> Settings:
    """Loads configuration from environment variables or .env file."""
    if env_file:
        dotenv.load_dotenv(dotenv_path=env_file)
    else:
        dotenv.load_dotenv()

    return Settings(
        bodhan_transcribe_key=os.environ.get("BODHAN_TRANSCRIBE_KEY", "").strip(),
        bodhan_translate_key=os.environ.get("BODHAN_TRANSLATE_KEY", "").strip(),
        bodhan_speak_key=os.environ.get("BODHAN_SPEAK_KEY", "").strip(),
        bodhan_ocr_key=os.environ.get("BODHAN_OCR_KEY", "").strip(),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").strip(),
        llm_model=os.environ.get("LLM_MODEL", "gpt-4o-mini").strip(),
        llm_api_key=os.environ.get("LLM_API_KEY", "").strip(),
    )


def validate_settings(settings: Settings) -> list[str]:
    """Validates presence of required keys, returning human-friendly missing key names."""
    missing = []
    if not settings.bodhan_transcribe_key:
        missing.append("BODHAN_TRANSCRIBE_KEY")
    if not settings.bodhan_translate_key:
        missing.append("BODHAN_TRANSLATE_KEY")
    if not settings.bodhan_speak_key:
        missing.append("BODHAN_SPEAK_KEY")
    if not settings.bodhan_ocr_key:
        missing.append("BODHAN_OCR_KEY")
    return missing
