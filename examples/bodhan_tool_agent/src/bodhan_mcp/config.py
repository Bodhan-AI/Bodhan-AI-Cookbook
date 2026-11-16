"""Server settings, read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODELS = ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")

KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}
FALLBACK_KEY_ENV = "BODHAN_API_KEY"  # Cookbook convention: one key for every model


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a pinned dependency
        return
    load_dotenv(Path.cwd() / ".env", override=False)


@dataclass
class Settings:
    base_url: str = "https://api.bodhan.ai"
    keys: dict[str, str | None] = field(default_factory=dict)
    cache_dir: Path = Path(".bodhan_cache")
    output_dir: Path = Path("bodhan_outputs")
    allowed_roots: list[Path] = field(default_factory=list)  # empty = any readable path
    max_concurrency: int = 4
    max_retries: int = 3
    backoff_base_s: float = 1.0
    backoff_cap_s: float = 20.0
    timeouts_s: dict[str, float] = field(default_factory=lambda: {
        "indic-transcribe": 60.0, "indic-translate": 60.0, "indic-speak": 90.0, "indic-ocr": 180.0,
    })
    use_cache: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv_if_present()
        fallback = os.environ.get(FALLBACK_KEY_ENV) or None
        keys = {model: (os.environ.get(env) or fallback) for model, env in KEY_ENV.items()}
        roots_env = os.environ.get("BODHAN_MCP_ROOTS", "")
        roots = [Path(p).expanduser().resolve() for p in roots_env.split(os.pathsep) if p.strip()]
        s = cls(
            base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
            keys=keys,
            cache_dir=Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")).expanduser().resolve(),
            output_dir=Path(os.environ.get("BODHAN_MCP_OUTPUT_DIR", "bodhan_outputs")).expanduser().resolve(),
            allowed_roots=roots,
            max_concurrency=max(1, _int("BODHAN_MAX_CONCURRENCY", 4)),
            max_retries=max(0, _int("BODHAN_MAX_RETRIES", 3)),
            use_cache=os.environ.get("BODHAN_CACHE", "1") not in ("0", "false", "off"),
        )
        scale = _float("BODHAN_TIMEOUT_SCALE", 1.0)
        s.timeouts_s = {k: v * scale for k, v in s.timeouts_s.items()}
        return s
