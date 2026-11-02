"""Settings, read from .env at the recipe root (see .env.example)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
RECIPE_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=RECIPE_ROOT / ".env", extra="ignore")

    # Bodhan keys work with one model each. bodhan_api_key, a key for every model, fills in for an empty one
    bodhan_transcribe_api_key: str = ""
    bodhan_translate_api_key: str = ""
    bodhan_api_key: str = ""
    bodhan_base_url: str = "https://api.bodhan.ai"

    # Ask indic-transcribe for word/segment timestamps (OpenAI verbose_json). Off by default: the API
    # accepts the request but returns no timings yet; turn on once Bodhan supports them.
    bodhan_timestamps: bool = False

    dev_mock_mode: bool = False
    max_upload_mb: int = 100
    max_video_seconds: int = 900
    chunk_seconds: float = 24.0
    ffmpeg_path: str = ""
    # Empty = SQLite file in backend/data/. For Postgres: postgresql+psycopg://user:password@host/db (and pip install psycopg)
    database_url: str = ""
    # Starting jobs from a link instead of an upload, one switch per kind of link:
    # a direct link to a video file (download.py)
    direct_video_links: bool = False
    # a YouTube video (youtube.py)
    youtube_links: bool = False
    # Folder of the built web app (frontend/dist) to serve at / on the same port as the API. Empty = don't
    # (the normal setup runs the web app with Vite). The Docker image sets it.
    web_dist: str = ""

    @property
    def transcribe_key(self) -> str:
        return self.bodhan_transcribe_api_key or self.bodhan_api_key

    @property
    def translate_key(self) -> str:
        return self.bodhan_translate_api_key or self.bodhan_api_key


settings = Settings()
