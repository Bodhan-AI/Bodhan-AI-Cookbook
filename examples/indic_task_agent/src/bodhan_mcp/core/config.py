"""Environment, per-model keys and the constants every tool shares."""
from __future__ import annotations

import os
from pathlib import Path

from .errors import BodhanError

MODELS = {
    "asr": "indic-transcribe",
    "mt": "indic-translate",
    "tts": "indic-speak",
    "ocr": "indic-ocr",
}
_KEY_VARS = {
    "asr": "BODHAN_TRANSCRIBE_KEY",
    "mt": "BODHAN_TRANSLATE_KEY",
    "tts": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}

ASR_MAX_WINDOW_S = 30.0
ASR_TARGET_WINDOW_S = 25.0
TTS_MAX_CLIP_CHARS = 400
MT_MAX_REQUEST_CHARS = 12_000
OCR_MAX_EDGE_PX = 2400
TTS_CHARS_PER_S = 13.5


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env reader; never overrides variables already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def base_url() -> str:
    return os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")


def api_key(kind: str) -> str:
    """Per-model key, falling back to BODHAN_API_KEY. Names the variable, never the value."""
    var = _KEY_VARS[kind]
    key = os.environ.get(var) or os.environ.get("BODHAN_API_KEY")
    if not key or key.startswith("<"):
        raise BodhanError(
            "AUTH_FAILED",
            f"No API key configured for {MODELS[kind]}.",
            hint=f"Set {var} (or BODHAN_API_KEY as a shared fallback).",
        )
    return key


def timeout_s(kind: str) -> float:
    default = 180.0 if kind in ("ocr", "tts") else 60.0
    return float(os.environ.get("BODHAN_TIMEOUT_S", default))


def max_concurrency() -> int:
    return max(1, int(os.environ.get("BODHAN_MAX_CONCURRENCY", "2")))


def cache_dir() -> Path:
    return Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan-cache"))


def default_out_dir() -> Path:
    return Path(".bodhan_out")
