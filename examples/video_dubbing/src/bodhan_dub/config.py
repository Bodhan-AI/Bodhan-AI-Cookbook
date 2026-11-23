"""
Application configuration and settings for bodhan_dub.
Loaded from environment variables, .env, and command-line flags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_cwd_env_file() -> Optional[str]:
    """E4: Finds .env strictly in current working directory (no ancestor traversal)."""
    candidate = Path.cwd() / ".env"
    return str(candidate) if candidate.is_file() else None


SILERO_VAD_PINNED_SHA256 = (
    "2ea52a1c618ec50428f5370d0b8b1a8d0f1b2123d47d953a99e71e74a80fb4ff"
)


class Settings(BaseSettings):
    """Core pipeline configuration and secrets."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def __init__(self, _env_file: Optional[Union[Path, str]] = None, **values: Any):
        # E4: Lazily resolve .env in cwd only, never searching ancestor directories
        if _env_file is None:
            _env_file = _get_cwd_env_file()
        super().__init__(_env_file=_env_file, **values)

    # Bodhan Endpoints & Keys
    bodhan_base_url: str = Field(
        default="https://api.bodhan.ai/v1",
        description="Base URL for Bodhan APIs",
    )
    bodhan_transcribe_key: str = Field(
        default="",
        description="API key for Indic-Transcribe",
    )
    bodhan_translate_key: str = Field(
        default="",
        description="API key for Indic-Translate",
    )
    bodhan_speak_key: str = Field(
        default="",
        description="API key for Indic-Speak",
    )
    bodhan_ocr_key: Optional[str] = Field(
        default=None,
        description="Optional API key for Indic-OCR",
    )

    # LLM Settings (English only)
    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI-compatible LLM endpoint",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="LLM model identifier",
    )
    llm_api_key: str = Field(
        default="",
        description="LLM API key",
    )

    # Concurrency Semaphores (Defaults per spec: ASR 4, Translate 4, TTS 3, LLM 4)
    semaphore_asr: int = 4
    semaphore_translate: int = 4
    semaphore_tts: int = 3
    semaphore_llm: int = 4

    # Timeouts in seconds
    timeout_asr: float = 60.0
    timeout_translate: float = 30.0
    timeout_tts: float = 60.0
    timeout_llm: float = 30.0

    # Pricing per spec (in INR)
    cost_asr_per_minute: float = 0.10
    cost_translate_per_10k_tokens: float = 0.20
    cost_tts_per_10k_chars: float = 6.00
    cost_ocr_per_image: float = 0.20

    # Cache Settings
    cache_dir: Path = Path(".cache")
    cache_version: str = "v1"

    # Circuit Breaker: consecutive 5xx failures to trigger fast fail for a language
    circuit_breaker_threshold: int = 5

    # QA Thresholds (CER, degraded cutoff, and script purity)
    cer_warn: float = 0.15
    cer_fail: float = 0.30
    cer_degraded_max: float = 0.45
    max_latin_share: float = 0.30

    def redact_key(self, key_str: str) -> str:
        """Helper to redact sensitive credentials for safe logging."""
        if not key_str:
            return "<not-set>"
        if len(key_str) <= 8:
            return "***"
        return f"{key_str[:4]}...{key_str[-4:]}"

    def get_sanitized_summary(self) -> dict:
        """Returns safe dictionary of settings for run reports without leaking secrets."""
        return {
            "bodhan_base_url": self.bodhan_base_url,
            "has_transcribe_key": bool(self.bodhan_transcribe_key),
            "has_translate_key": bool(self.bodhan_translate_key),
            "has_speak_key": bool(self.bodhan_speak_key),
            "has_ocr_key": bool(self.bodhan_ocr_key),
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "has_llm_key": bool(self.llm_api_key),
            "semaphores": {
                "asr": self.semaphore_asr,
                "translate": self.semaphore_translate,
                "tts": self.semaphore_tts,
                "llm": self.semaphore_llm,
            },
        }
