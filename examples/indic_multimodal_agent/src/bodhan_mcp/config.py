"""Configuration management for Bodhan MCP Server and Task Agent.
Loads API keys and settings from environment variables and .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Search for .env in current working dir and up to project root
for p in [Path.cwd(), Path.cwd() / "bodhan-agent-project", Path(__file__).resolve().parents[2]]:
    env_file = p / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        break


class Settings:
    # Bodhan API Keys
    BODHAN_TRANSCRIBE_KEY: str = os.getenv("BODHAN_TRANSCRIBE_KEY", "")
    BODHAN_TRANSLATE_KEY: str = os.getenv("BODHAN_TRANSLATE_KEY", "")
    BODHAN_SPEAK_KEY: str = os.getenv("BODHAN_SPEAK_KEY", "")
    BODHAN_OCR_KEY: str = os.getenv("BODHAN_OCR_KEY", "")

    # Bodhan Base URL
    BODHAN_BASE_URL: str = os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")

    # LLM Settings (OpenAI-compatible)
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gpt-4o")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")

    # Concurrency and Rate Limits
    MAX_CONCURRENT_CALLS: int = int(os.getenv("MAX_CONCURRENT_CALLS", "5"))  # Safe limit below 10-parallel
    REQUEST_TIMEOUT_S: float = float(os.getenv("REQUEST_TIMEOUT_S", "30.0"))

    # Caching
    CACHE_DIR: Path = Path(os.getenv("CACHE_DIR", "./.bodhan_cache"))


settings = Settings()

