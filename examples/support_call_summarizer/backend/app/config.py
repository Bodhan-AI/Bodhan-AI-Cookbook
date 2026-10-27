"""Settings read from environment variables or the recipe-root ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RECIPE_ROOT = Path(__file__).resolve().parents[2]

ALLOWED_EXTENSIONS = (".wav", ".mp3", ".m4a", ".aac", ".amr", ".ogg", ".flac", ".webm")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(RECIPE_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Bodhan
    bodhan_api_key: str = Field(default="", description="used for any model without its own key below")
    bodhan_transcribe_api_key: str = ""
    bodhan_translate_api_key: str = ""
    bodhan_speak_api_key: str = Field(default="", description="only for scripts/make_sample_call.py")
    bodhan_base_url: str = "https://api.bodhan.ai"
    bodhan_timeout_seconds: float = 60.0
    bodhan_max_retries: int = 4
    bodhan_concurrency: int = Field(default=4, description="parallel Bodhan requests per call")
    bodhan_rpm: int = Field(default=0, description="client-side requests/minute cap; 0 = no cap")

    # Sarvam LLM: call summary and sentiment (optional; off when the key is empty)
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_model: str = "sarvam-105b"
    sarvam_reasoning_effort: str = Field(default="low", description="'low', 'high', 'max', or 'none' to turn it off")
    sarvam_max_tokens: int = Field(default=4096, description="answer plus reasoning; the Starter plan allows 4096")
    sarvam_timeout_seconds: float = 120.0
    sarvam_max_retries: int = 4
    sarvam_rpm: int = Field(default=0, description="client-side requests/minute cap; 0 = no cap")

    # Claude: fallback LLM for the analysis when Sarvam fails (optional; off when the key is empty)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = Field(default="medium", description="'low', 'medium', 'high', 'xhigh' or 'max'")
    anthropic_max_tokens: int = 16000
    anthropic_timeout_seconds: float = 120.0
    anthropic_max_retries: int = 4

    # Speaker diarization (optional, see diarization.py)
    diarization_provider: str = Field(
        default="none", description="'none' or 'nemotron' (local NVIDIA GPU; turns itself off if the GPU is too small)"
    )
    diarization_model: str = "nvidia/Nemotron-3-Diarization"
    diarization_min_vram_gb: float = Field(default=2.0, description="smallest total GPU memory 'nemotron' will use")
    max_speakers: int = 8

    # Limits (FR-1)
    max_file_mb: int = 50
    max_duration_seconds: int = 30 * 60
    max_piece_seconds: float = 28.0

    # Jobs (FR-12)
    result_ttl_seconds: int = 3600
    max_concurrent_jobs: int = 2
    timezone: str = "Asia/Kolkata"

    # App -> backend
    app_key: str = Field(default="", description="shared key the app sends in X-App-Key; empty = not required")
    cors_origins: str = Field(default="", description="comma-separated origins; empty = same-origin only")
    web_dist_dir: Path = RECIPE_ROOT / "web" / "dist"

    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    @property
    def analysis_enabled(self) -> bool:
        return bool(self.sarvam_api_key or self.anthropic_api_key)

    @property
    def transcribe_key(self) -> str:
        return self.bodhan_transcribe_api_key or self.bodhan_api_key

    @property
    def translate_key(self) -> str:
        return self.bodhan_translate_api_key or self.bodhan_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
