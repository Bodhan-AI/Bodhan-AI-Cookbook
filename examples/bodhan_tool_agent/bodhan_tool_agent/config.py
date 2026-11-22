from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required Bodhan Tool Agent configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    transcribe_key: str | None
    translate_key: str | None
    speak_key: str | None
    ocr_key: str | None
    base_url: str
    timeout: float
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> Settings:
        if load_dotenv_file:
            load_dotenv()

        fallback = _clean(os.environ.get("BODHAN_API_KEY"))
        settings = cls(
            transcribe_key=_clean(os.environ.get("BODHAN_TRANSCRIBE_KEY")) or fallback,
            translate_key=_clean(os.environ.get("BODHAN_TRANSLATE_KEY")) or fallback,
            speak_key=_clean(os.environ.get("BODHAN_SPEAK_KEY")) or fallback,
            ocr_key=_clean(os.environ.get("BODHAN_OCR_KEY")) or fallback,
            base_url=_clean(os.environ.get("BODHAN_BASE_URL")) or "https://api.bodhan.ai",
            timeout=_parse_timeout(os.environ.get("BODHAN_TIMEOUT")),
            llm_base_url=_clean(os.environ.get("LLM_BASE_URL")),
            llm_model=_clean(os.environ.get("LLM_MODEL")),
            llm_api_key=_clean(os.environ.get("LLM_API_KEY")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigurationError("BODHAN_BASE_URL must be an absolute https URL")
        if self.timeout <= 0:
            raise ConfigurationError("BODHAN_TIMEOUT must be a positive number")

    def key_for(self, model: str) -> str:
        keys = {
            "indic-transcribe": self.transcribe_key,
            "indic-translate": self.translate_key,
            "indic-speak": self.speak_key,
            "indic-ocr": self.ocr_key,
        }
        key = keys.get(model)
        if not key:
            raise ConfigurationError(
                f"No API key configured for {model}; set its model-specific key or BODHAN_API_KEY"
            )
        return key


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _parse_timeout(value: str | None) -> float:
    if value is None:
        return 60.0
    try:
        timeout = float(value)
    except ValueError as exc:
        raise ConfigurationError("BODHAN_TIMEOUT must be a number") from exc
    return timeout
