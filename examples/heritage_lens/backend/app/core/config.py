from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICES = ("transcribe", "translate", "transliterate", "ocr", "speak")
logger = logging.getLogger("heritagelens.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_name: str = "HeritageLens"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./heritagelens.db"
    secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12

    storage_dir: Path = Path("./storage")
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    max_audio_bytes: int = 50 * 1024 * 1024
    max_image_bytes: int = 15 * 1024 * 1024
    max_document_bytes: int = 25 * 1024 * 1024
    max_audio_seconds: float = 3600.0

    # ---- Bodhan ----
    bodhan_base_url: str = "https://api.bodhan.ai"
    bodhan_api_key: SecretStr | None = None
    bodhan_transcribe_api_key: SecretStr | None = None
    bodhan_translate_api_key: SecretStr | None = None
    bodhan_transliterate_api_key: SecretStr | None = None
    bodhan_ocr_api_key: SecretStr | None = None
    bodhan_speak_api_key: SecretStr | None = None

    bodhan_max_retries: int = 2
    bodhan_backoff_base_seconds: float = 0.5
    bodhan_connect_timeout: float = 10.0
    transcribe_timeout: float = 120.0
    translate_timeout: float = 60.0
    transliterate_timeout: float = 60.0
    speak_timeout: float = 120.0
    ocr_timeout: float = 180.0

    # Documented hard limit is 30 s; we default lower so rounding in the
    # server's duration measurement can never push a segment over the limit.
    transcribe_segment_seconds: float = 29.0
    # The docs do not publish a text-size limit for /translate or /transliterate
    # ("payload_too_large" exists), so chunk conservatively. Tunable.
    translate_chunk_chars: int = 2000
    transliterate_chunk_chars: int = 2000
    # Docs: "a sentence or two, about 30 seconds of speech". Tunable.
    speak_chunk_chars: int = 200
    speak_gap_ms: int = 150

    ocr_pdf_dpi: int = 200
    ocr_max_pdf_pages: int = 50
    ocr_max_tokens_cap: int = 8192

    ffmpeg_path: str | None = None
    ffmpeg_timeout_seconds: float = 300.0

    @model_validator(mode="after")
    def _check_secret(self) -> "Settings":
        if not self.secret_key or len(self.secret_key) < 32:
            if self.app_env == "production":
                raise ValueError("SECRET_KEY must be set to at least 32 characters in production.")
            if not self.secret_key:
                self.secret_key = secrets.token_urlsafe(48)
                logger.warning(
                    "SECRET_KEY not set; generated an ephemeral key. Logins will not survive a restart."
                )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]

    @staticmethod
    def _usable(candidate) -> str | None:
        if candidate is None:
            return None
        value = candidate.get_secret_value().strip()
        if not value or value.lower().startswith(("your-", "your_", "changeme", "change-me")):
            return None  # an untouched placeholder from .env.example is not a key
        return value

    def key_for(self, service: str) -> str | None:
        """Service-specific key if set, otherwise the shared key, otherwise None."""
        if service not in SERVICES:
            raise ValueError(f"Unknown Bodhan service: {service}")
        return self._usable(getattr(self, f"bodhan_{service}_api_key")) or self._usable(self.bodhan_api_key)

    def key_source(self, service: str) -> str | None:
        """'service', 'shared' or None. Never returns the key itself."""
        if self._usable(getattr(self, f"bodhan_{service}_api_key")):
            return "service"
        return "shared" if self._usable(self.bodhan_api_key) else None

    def configured_services(self) -> dict[str, bool]:
        return {s: self.key_for(s) is not None for s in SERVICES}

    def timeout_for(self, service: str) -> float:
        return getattr(self, f"{service}_timeout")

    def all_secret_values(self) -> list[str]:
        values = [self.secret_key or ""]
        for name in ("bodhan_api_key",) + tuple(f"bodhan_{s}_api_key" for s in SERVICES):
            v = getattr(self, name)
            if v is not None:
                values.append(v.get_secret_value())
        return [v for v in values if v]


@lru_cache
def get_settings() -> Settings:
    return Settings()
