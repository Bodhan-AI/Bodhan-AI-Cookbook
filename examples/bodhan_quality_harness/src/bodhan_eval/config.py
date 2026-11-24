"""Configuration: env, models, cost table, thresholds.

Everything tunable lives here so the calibration step can override it from data
rather than anyone editing constants in the check code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _refresh_from_env() -> None:
    global BODHAN_BASE_URL, BODHAN_TRANSLATE_KEY, BODHAN_SPEAK_KEY, BODHAN_TRANSCRIBE_KEY
    global TRANSLATE_MODEL, SPEAK_MODEL, TRANSCRIBE_MODEL
    global LLM_BASE_URL, LLM_MODEL, LLM_API_KEY
    BODHAN_BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")
    _api_key = os.environ.get("BODHAN_API_KEY", "")
    BODHAN_TRANSLATE_KEY = os.environ.get("BODHAN_TRANSLATE_KEY") or _api_key
    BODHAN_SPEAK_KEY = os.environ.get("BODHAN_SPEAK_KEY") or _api_key
    BODHAN_TRANSCRIBE_KEY = os.environ.get("BODHAN_TRANSCRIBE_KEY") or _api_key

    TRANSLATE_MODEL = os.environ.get("BODHAN_TRANSLATE_MODEL", "indic-translate")
    SPEAK_MODEL = os.environ.get("BODHAN_SPEAK_MODEL", "indic-speak")
    TRANSCRIBE_MODEL = os.environ.get("BODHAN_TRANSCRIBE_MODEL", "indic-transcribe")

    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
    LLM_MODEL = os.environ.get("LLM_MODEL", "")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")


def load_env(path: str | Path = ".env") -> None:
    """Minimal .env loader so we do not pull in a dependency for four keys."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _refresh_from_env()


# --- Bodhan hosted API --------------------------------------------------------
BODHAN_BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai/v1")
_api_key = os.environ.get("BODHAN_API_KEY", "")
BODHAN_TRANSLATE_KEY = os.environ.get("BODHAN_TRANSLATE_KEY") or _api_key
BODHAN_SPEAK_KEY = os.environ.get("BODHAN_SPEAK_KEY") or _api_key
BODHAN_TRANSCRIBE_KEY = os.environ.get("BODHAN_TRANSCRIBE_KEY") or _api_key

TRANSLATE_MODEL = os.environ.get("BODHAN_TRANSLATE_MODEL", "indic-translate")
SPEAK_MODEL = os.environ.get("BODHAN_SPEAK_MODEL", "indic-speak")
TRANSCRIBE_MODEL = os.environ.get("BODHAN_TRANSCRIBE_MODEL", "indic-transcribe")

# --- Judge LLM ---------------------------------------------------------------
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

# --- Cost table (rupees), from the assignment brief --------------------------
COST_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
COST_SPEAK_PER_10K_CHARS = 6.00
COST_TRANSCRIBE_PER_AUDIO_MINUTE = 0.10

# Rough output-token estimate for translation: Indic scripts cost more tokens
# per character than Latin, so this is deliberately conservative.
TOKENS_PER_CHAR = 0.6

# --- Thresholds (overridden by calibration) ----------------------------------
@dataclass
class Thresholds:
    length_lo: float = 0.55
    length_hi: float = 2.20
    script_min: float = 0.35
    speech_similarity_min: float = 0.72   # back-transcription vs source
    duration_ratio_lo: float = 0.55
    duration_ratio_hi: float = 2.00
    silence_max_fraction: float = 0.35
    clipping_max_fraction: float = 0.01


@dataclass
class RunConfig:
    dataset: Path
    langs: list[str]
    out: Path
    generate: bool = False
    baseline: Path | None = None
    calibration: Path | None = None
    max_cost_inr: float | None = None
    cache_dir: Path = Path(".cache/bodhan_eval")
    concurrency: int = 4
    seed: int = 7
    offline: bool = False          # force no model calls (tests, dry runs)
    thresholds: Thresholds = field(default_factory=Thresholds)


def have_translate() -> bool:
    return bool(BODHAN_TRANSLATE_KEY or os.environ.get("BODHAN_TRANSLATE_KEY"))


def have_speak() -> bool:
    return bool(BODHAN_SPEAK_KEY or os.environ.get("BODHAN_SPEAK_KEY"))


def have_transcribe() -> bool:
    return bool(BODHAN_TRANSCRIBE_KEY or os.environ.get("BODHAN_TRANSCRIBE_KEY"))


def have_judge() -> bool:
    base = LLM_BASE_URL or os.environ.get("LLM_BASE_URL")
    model = LLM_MODEL or os.environ.get("LLM_MODEL")
    key = LLM_API_KEY or os.environ.get("LLM_API_KEY")
    return bool(base and model and key)
