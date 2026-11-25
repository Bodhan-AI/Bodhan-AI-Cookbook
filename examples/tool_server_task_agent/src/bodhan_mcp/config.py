"""Configuration: model keys (with fallbacks), work/cache dirs, limits, and the Bodhan API rules."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .errors import ToolFailure

BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")

# Official names first; the second name is a local-convenience fallback only.
KEY_ENV: dict[str, tuple[str, ...]] = {
    "indic-transcribe": ("BODHAN_TRANSCRIBE_KEY", "INDIC_TRANSCRIBE_API_KEY"),
    "indic-translate": ("BODHAN_TRANSLATE_KEY", "INDIC_TRANSLATE"),
    "indic-speak": ("BODHAN_SPEAK_KEY", "INDIC_SPEAK"),
    "indic-ocr": ("BODHAN_OCR_KEY", "INDIC_OCR"),
}

_ENV_LOADED = False


def load_env() -> None:
    """Load the nearest .env from the working dir or any parent (e.g. repo root). Real environment variables win."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for parent in [start, *start.parents]:
            candidate = parent / ".env"
            if candidate.is_file():
                load_dotenv(candidate, override=False)
                return


def get_key(model: str) -> str:
    load_env()
    for name in KEY_ENV[model]:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ToolFailure(
        "MISSING_API_KEY",
        f"No API key configured for {model}.",
        hint=f"Set {KEY_ENV[model][0]} in the environment or .env (see .env.example).",
    )


def have_key(model: str) -> bool:
    try:
        get_key(model)
        return True
    except ToolFailure:
        return False


def work_dir() -> Path:
    p = Path(os.environ.get("BODHAN_WORKDIR", ".bodhan_work")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def max_concurrency() -> int:
    return max(1, int(os.environ.get("BODHAN_MAX_CONCURRENCY", "3")))


def call_timeout_s() -> float:
    return float(os.environ.get("BODHAN_CALL_TIMEOUT_S", "90"))


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads((Path(__file__).parent / "data" / "bodhan_api_rules.json").read_text(encoding="utf-8"))


def language_names() -> dict[str, str]:
    return rules()["language_names"]


def languages_for(model: str) -> list[str]:
    return list(rules()["models"][model]["languages"])


def voices() -> dict[str, dict[str, list[str]]]:
    return rules()["models"]["indic-speak"]["voices"]


def speak_styles() -> list[str]:
    return list(rules()["models"]["indic-speak"]["styles"])


# Hard limits enforced inside tools so callers never have to.
ASR_WINDOW_MAX_S = 28.0           # Bodhan accepts ~30 s; stay under it
ASR_WINDOW_MIN_S = 6.0
TTS_REQUEST_MAX_CHARS = 220       # "a sentence or two" = under 30 s of speech
TRANSLATE_CHUNK_MAX_CHARS = 1500  # far inside the 32K context; keeps outputs well under max tokens
MAX_TEXT_CHARS = 150_000          # refuse bigger inputs (~100 chunks): protects latency and cost
MAX_TTS_CHARS = 20_000            # = Rs 12 at published prices
MAX_OCR_PAGES = 30
MAX_AUDIO_SECONDS = 3 * 3600
