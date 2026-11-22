"""Settings read from the environment. No secrets live in code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Hosted prices from the assignment brief, in rupees.
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20

# Model limits, handled inside the tools so callers never see them.
MAX_AUDIO_WINDOW_S = 28.0  # the API accepts about 30 s; keep a margin
MAX_TTS_CHARS = 280  # "a sentence or two" per request
MAX_TRANSLATE_CHARS = 1800  # per request; far below the 32K-token context
MAX_OCR_PAGES = 40

KEY_VARS = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}


@dataclass
class Settings:
    base_url: str = "https://api.bodhan.ai"
    keys: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 120.0
    max_retries: int = 4
    max_parallel: int = 4
    cache_dir: Path = Path(".bodhan_cache")
    max_cost_inr: float | None = None

    @classmethod
    def from_env(cls, **overrides) -> Settings:
        base = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")
        if base.endswith("/v1"):  # accept either form; /translate lives at the root
            base = base[: -len("/v1")]
        shared = os.environ.get("BODHAN_API_KEY", "")
        keys = {name: os.environ.get(var, "") or shared for name, var in KEY_VARS.items()}
        settings = cls(
            base_url=base,
            keys=keys,
            timeout_s=float(os.environ.get("BODHAN_TIMEOUT_S", "120")),
            max_parallel=int(os.environ.get("BODHAN_MAX_PARALLEL", "4")),
            cache_dir=Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")),
        )
        for name, value in overrides.items():
            setattr(settings, name, value)
        return settings
