"""Runtime settings, read from the environment (and an optional .env file).

Only these secrets are read: BODHAN_TRANSLATE_KEY, BODHAN_SPEAK_KEY, BODHAN_TRANSCRIBE_KEY
(each falls back to BODHAN_API_KEY) and LLM_API_KEY.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency
        return
    # Current directory first, then the recipe folder. Existing env vars always win.
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _key(name: str) -> str:
    return os.environ.get(name) or os.environ.get("BODHAN_API_KEY", "")


@dataclass
class Settings:
    bodhan_base_url: str = "https://api.bodhan.ai"
    translate_key: str = ""
    speak_key: str = ""
    transcribe_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_extra_body: dict = field(default_factory=dict)
    llm_tokens_per_minute: int = 0  # 0 = no client-side limit
    bodhan_concurrency: int = 4
    llm_concurrency: int = 4
    cache_dir: Path = Path(".bodhan_eval_cache")
    request_timeout_s: float = 180.0
    max_retries: int = 8  # with backoff capped at 30 s this rides out ~2.5 min of throttling

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @classmethod
    def from_env(cls, cache_dir: Path | None = None) -> Settings:
        _load_dotenv()
        extra = os.environ.get("LLM_EXTRA_BODY", "").strip()
        return cls(
            bodhan_base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
            translate_key=_key("BODHAN_TRANSLATE_KEY"),
            speak_key=_key("BODHAN_SPEAK_KEY"),
            transcribe_key=_key("BODHAN_TRANSCRIBE_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", ""),
            llm_model=os.environ.get("LLM_MODEL", ""),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_extra_body=json.loads(extra) if extra else {},
            llm_tokens_per_minute=int(os.environ.get("LLM_TOKENS_PER_MINUTE", "0") or 0),
            bodhan_concurrency=int(os.environ.get("BODHAN_CONCURRENCY", "4")),
            llm_concurrency=int(os.environ.get("LLM_CONCURRENCY", "4")),
            cache_dir=Path(cache_dir or os.environ.get("BODHAN_EVAL_CACHE", ".bodhan_eval_cache")),
        )
