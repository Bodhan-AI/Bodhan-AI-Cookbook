"""Configuration, model limits and prices.

Everything that a reviewer might want to tune lives here, in one place.
Keys are read from the environment only; nothing is ever written to disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("api_rules.json")

# Hosted prices in rupees (from the assignment brief).
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20

# Model limits handled inside the tools so callers never see them.
ASR_WINDOW_SEC = 28.0          # hard limit is ~30 s; keep a safety margin
ASR_MIN_WINDOW_SEC = 8.0       # do not cut windows shorter than this when looking for silence
TTS_MAX_CHARS = 220            # "a sentence or two" per request
TRANSLATE_MAX_CHARS = 6000     # per request; far below the 32K-token context, keeps latency low
OCR_PDF_DPI = 170              # 150-200 DPI recommended by the Cookbook OCR guide
OCR_MAX_PAGES = 60


def _key(model_env: str) -> str | None:
    """Per-model key (assignment spec) with BODHAN_API_KEY as fallback (Cookbook convention)."""
    return os.environ.get(model_env) or os.environ.get("BODHAN_API_KEY")


@dataclass
class Settings:
    base_url: str = field(default_factory=lambda: os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"))
    transcribe_key: str | None = field(default_factory=lambda: _key("BODHAN_TRANSCRIBE_KEY"))
    translate_key: str | None = field(default_factory=lambda: _key("BODHAN_TRANSLATE_KEY"))
    speak_key: str | None = field(default_factory=lambda: _key("BODHAN_SPEAK_KEY"))
    ocr_key: str | None = field(default_factory=lambda: _key("BODHAN_OCR_KEY"))
    cache_dir: Path = field(default_factory=lambda: Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")))
    work_dir: Path = field(default_factory=lambda: Path(os.environ.get("BODHAN_WORK_DIR", "outputs/mcp_artifacts")))
    max_parallel: int = field(default_factory=lambda: int(os.environ.get("BODHAN_MAX_PARALLEL", "2")))
    request_timeout: float = field(default_factory=lambda: float(os.environ.get("BODHAN_TIMEOUT_SEC", "120")))
    max_retries: int = 4


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def languages(model: str) -> list[str]:
    return list(rules()["models"][model]["languages"])


def language_name(code: str) -> str:
    return rules()["language_names"].get(code, code)


def voices(lang: str) -> dict[str, list[str]]:
    return rules()["models"]["indic-speak"]["voices"].get(lang, {})


def styles() -> list[str]:
    return list(rules()["models"]["indic-speak"]["styles"])
