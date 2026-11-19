"""Configuration management for Bodhan MCP Server and Task Agent.
Loads API keys and settings from environment variables and .env file.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Search for .env in current working dir and up to project root
for p in [Path.cwd(), Path.cwd() / "bodhan-agent-project", Path(__file__).resolve().parents[2]]:
    env_file = p / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        break


class Settings:
    def __init__(self):
        # Bodhan API Keys
        self.BODHAN_TRANSCRIBE_KEY: str = os.getenv("BODHAN_TRANSCRIBE_KEY", "")
        self.BODHAN_TRANSLATE_KEY: str = os.getenv("BODHAN_TRANSLATE_KEY", "")
        self.BODHAN_SPEAK_KEY: str = os.getenv("BODHAN_SPEAK_KEY", "")
        self.BODHAN_OCR_KEY: str = os.getenv("BODHAN_OCR_KEY", "")

        # Bodhan Base URL
        self.BODHAN_BASE_URL: str = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")

        # LLM Settings (OpenAI-compatible)
        self.LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini-flash-lite-latest")
        self.LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

        # Concurrency and Rate Limits
        self.MAX_CONCURRENT_CALLS: int = int(os.getenv("MAX_CONCURRENT_CALLS", "5"))  # Safe limit below 10-parallel
        self.REQUEST_TIMEOUT_S: float = float(os.getenv("REQUEST_TIMEOUT_S", "30.0"))

        # Caching
        self.CACHE_DIR: Path = Path(os.getenv("CACHE_DIR", "./.bodhan_cache"))

        self._llm_base_url: Optional[str] = os.getenv("LLM_BASE_URL")

    @property
    def LLM_BASE_URL(self) -> str:
        url = (os.getenv("LLM_BASE_URL") or self._llm_base_url or "").strip()
        if not url:
            raise ValueError(
                "LLM_BASE_URL is required. Set LLM_BASE_URL in your environment or .env file "
                "(e.g., https://generativelanguage.googleapis.com/v1beta/openai/)."
            )
        return url

    @LLM_BASE_URL.setter
    def LLM_BASE_URL(self, value: str) -> None:
        self._llm_base_url = value


settings = Settings()
