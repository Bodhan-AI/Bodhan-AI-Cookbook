"""Settings are read from the environment on every access so the agent can change them at runtime."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = find_dotenv = None

KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}


def load_env() -> None:
    """Load .env from the current working directory (or a parent of it), never from the package folder."""
    if load_dotenv:
        load_dotenv(find_dotenv(usecwd=True))


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    base_url: str
    work_dir: Path
    cache_dir: Path
    concurrency: int
    http_timeout_s: float
    max_retries: int
    tts_max_chars: int
    translate_max_chars: int
    asr_max_window_s: float
    cer_threshold: float

    def key_for(self, service: str) -> str | None:
        return os.environ.get(KEY_ENV[service]) or os.environ.get("BODHAN_API_KEY") or None


def get_settings() -> Settings:
    return Settings(
        base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
        work_dir=Path(os.environ.get("BODHAN_WORK_DIR", "./bodhan_work")).resolve(),
        cache_dir=Path(os.environ.get("BODHAN_CACHE_DIR", "./.bodhan_cache")).resolve(),
        concurrency=int(_f("BODHAN_CONCURRENCY", 4)),
        http_timeout_s=_f("BODHAN_HTTP_TIMEOUT_S", 90),
        max_retries=int(_f("BODHAN_MAX_RETRIES", 4)),
        tts_max_chars=int(_f("BODHAN_TTS_MAX_CHARS", 240)),
        translate_max_chars=int(_f("BODHAN_TRANSLATE_MAX_CHARS", 4000)),
        asr_max_window_s=min(_f("BODHAN_ASR_WINDOW_S", 28.0), 29.5),  # API limit is 30 s
        cer_threshold=_f("BODHAN_CER_THRESHOLD", 0.40),
    )
