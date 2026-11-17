"""Configuration: env, models, cost table, thresholds.

Endpoint paths and model ids follow the Cookbook's ``scripts/bodhan_api_rules.json``
and the calling convention used by ``examples/voice_notice_translator``:

    POST /translate                    {"text", "target_language"} -> {"translation"}
    POST /v1/audio/speech              {"model", "input", "voice"}  -> audio/wav
    POST /v1/audio/transcriptions      multipart + {"model", "language"} -> {"text"}

``indic-translate`` is **not** OpenAI-compatible; only the audio endpoints are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    """Minimal .env loader so we do not pull in a dependency for a few keys."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# --- Bodhan hosted API --------------------------------------------------------
BODHAN_BASE_URL = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai")

# One key from console.bodhan.ai covers the models it was issued for. Per-model
# keys are also supported for accounts that scope them separately.
BODHAN_API_KEY = os.environ.get("BODHAN_API_KEY", "")
BODHAN_TRANSLATE_KEY = os.environ.get("BODHAN_TRANSLATE_KEY", "")
BODHAN_SPEAK_KEY = os.environ.get("BODHAN_SPEAK_KEY", "")
BODHAN_TRANSCRIBE_KEY = os.environ.get("BODHAN_TRANSCRIBE_KEY", "")

TRANSLATE_MODEL = os.environ.get("BODHAN_TRANSLATE_MODEL", "indic-translate")
SPEAK_MODEL = os.environ.get("BODHAN_SPEAK_MODEL", "indic-speak")
TRANSCRIBE_MODEL = os.environ.get("BODHAN_TRANSCRIBE_MODEL", "indic-transcribe")

# Indic-Speak uses a fixed voice library; these are the names the Cookbook uses.
VOICES = {
    "en": "Amit", "hi": "Ishita", "bn": "Ishita", "ta": "Anitha",
    "te": "Sravani", "mr": "Anagha", "gu": "Ishita", "kn": "Sravani",
    "ml": "Anagha", "pa": "Amit", "or": "Ishita",
}
SPEAK_VOICE = os.environ.get("BODHAN_SPEAK_VOICE", "")

# --- Judge LLM (any OpenAI-compatible endpoint) -------------------------------
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")

# --- Cost table (rupees), from the assignment brief --------------------------
COST_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
COST_SPEAK_PER_10K_CHARS = 6.00
COST_TRANSCRIBE_PER_AUDIO_MINUTE = 0.10

# Indic scripts cost more tokens per character than Latin; deliberately high.
TOKENS_PER_CHAR = 0.6

# Indic-Transcribe accepts ~30 s of audio per request.
MAX_AUDIO_SECONDS = 30.0


@dataclass
class Thresholds:
    length_lo: float = 0.55
    length_hi: float = 2.20
    script_min: float = 0.35
    speech_similarity_min: float = 0.72
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
    offline: bool = False
    thresholds: Thresholds = field(default_factory=Thresholds)


def key_for(model: str) -> str:
    """Per-model key if set, else the shared BODHAN_API_KEY."""
    specific = {
        "translate": BODHAN_TRANSLATE_KEY,
        "speak": BODHAN_SPEAK_KEY,
        "transcribe": BODHAN_TRANSCRIBE_KEY,
    }.get(model, "")
    return specific or BODHAN_API_KEY


def have_translate() -> bool:
    return bool(key_for("translate"))


def have_speak() -> bool:
    return bool(key_for("speak"))


def have_transcribe() -> bool:
    return bool(key_for("transcribe"))


def have_judge() -> bool:
    return bool(LLM_BASE_URL and LLM_MODEL and LLM_API_KEY)


def voice_for(lang: str) -> str:
    return SPEAK_VOICE or VOICES.get((lang or "").split("-")[0], "Amit")
