"""Runtime configuration, read from the environment once per process.

Keys: one per model (BODHAN_TRANSCRIBE_KEY, BODHAN_TRANSLATE_KEY, BODHAN_SPEAK_KEY,
BODHAN_OCR_KEY). BODHAN_API_KEY is accepted as a fallback for any of them, so the
Cookbook convention of a single key also works.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODEL_KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}

# Hosted prices in INR (from the Bodhan console).
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    base_url: str = "https://api.bodhan.ai"
    keys: dict[str, str | None] = field(default_factory=dict)
    workdir: Path = Path("bodhan_mcp_out")
    cache_dir: Path = Path(".bodhan_cache")
    cache_enabled: bool = True
    call_timeout_s: float = 120.0
    max_retries: int = 4
    concurrency: int = 4
    # Indic-Speak and Indic-Transcribe return 429 at four parallel requests, so they get a lower default.
    model_concurrency: dict[str, int] = field(default_factory=lambda: {"speak": 2, "transcribe": 2})
    max_cost_inr: float | None = None
    allowed_roots: list[Path] | None = None  # None = any readable path

    @classmethod
    def from_env(cls) -> Settings:
        fallback = os.environ.get("BODHAN_API_KEY") or None
        keys = {m: (os.environ.get(env) or fallback) for m, env in MODEL_KEY_ENV.items()}
        roots_env = os.environ.get("BODHAN_ALLOWED_DIRS")
        roots = [Path(p).expanduser().resolve() for p in roots_env.split(os.pathsep) if p] if roots_env else None
        max_cost = os.environ.get("BODHAN_MAX_COST_INR")
        defaults = {"speak": 2, "transcribe": 2}
        model_concurrency = {
            m: max(1, _int_env(f"BODHAN_CONCURRENCY_{m.upper()}", defaults.get(m, 0)))
            for m in MODEL_KEY_ENV
            if os.environ.get(f"BODHAN_CONCURRENCY_{m.upper()}") or m in defaults
        }
        return cls(
            base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/").removesuffix("/v1"),
            keys=keys,
            workdir=Path(os.environ.get("BODHAN_WORKDIR", "bodhan_mcp_out")).expanduser().resolve(),
            cache_dir=Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")).expanduser().resolve(),
            cache_enabled=os.environ.get("BODHAN_CACHE", "1") not in {"0", "false", "no"},
            call_timeout_s=_float_env("BODHAN_CALL_TIMEOUT_S", 120.0),
            max_retries=_int_env("BODHAN_MAX_RETRIES", 4),
            concurrency=max(1, _int_env("BODHAN_CONCURRENCY", 4)),
            model_concurrency=model_concurrency,
            max_cost_inr=float(max_cost) if max_cost else None,
            allowed_roots=roots,
        )
