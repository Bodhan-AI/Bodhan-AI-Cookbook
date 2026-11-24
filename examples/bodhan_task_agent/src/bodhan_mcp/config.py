"""Server configuration, read once from the environment.

Only the variables the assignment names are secrets: ``BODHAN_*_KEY`` (plus the
Cookbook's ``BODHAN_API_KEY`` fallback). Everything else is optional tuning.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import errors

# Model id -> (per-model key variable, endpoint path)
MODEL_KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


@dataclass
class Settings:
    base_url: str = "https://api.bodhan.ai"
    keys: dict[str, str] = field(default_factory=dict)
    cache_dir: Path = field(default_factory=lambda: Path("~/.cache/bodhan_mcp").expanduser())
    cache_enabled: bool = True
    workdir: Path = Path("bodhan_mcp_outputs")
    allowed_dirs: list[Path] = field(default_factory=list)
    max_concurrency: int = 4
    max_retries: int = 4
    # Per-call timeouts in seconds, by model.
    timeouts: dict[str, float] = field(
        default_factory=lambda: {
            "indic-transcribe": 90.0,
            "indic-translate": 120.0,
            "indic-speak": 120.0,
            "indic-ocr": 180.0,
        }
    )
    http_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        fallback = os.environ.get("BODHAN_API_KEY", "").strip()
        keys = {}
        for model, var in MODEL_KEY_ENV.items():
            value = os.environ.get(var, "").strip() or fallback
            if value:
                keys[model] = value
        allowed = [Path(p).expanduser().resolve() for p in os.environ.get("BODHAN_MCP_ALLOWED_DIRS", "").split(os.pathsep) if p]
        s = cls(
            base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
            keys=keys,
            cache_dir=Path(os.environ.get("BODHAN_CACHE_DIR", "~/.cache/bodhan_mcp")).expanduser(),
            cache_enabled=os.environ.get("BODHAN_CACHE", "1") not in {"0", "false", "off"},
            workdir=Path(os.environ.get("BODHAN_MCP_WORKDIR", "bodhan_mcp_outputs")).expanduser(),
            allowed_dirs=allowed,
            max_concurrency=max(1, _env_int("BODHAN_MAX_CONCURRENCY", 4)),
            max_retries=max(0, _env_int("BODHAN_MAX_RETRIES", 4)),
            http_token=os.environ.get("BODHAN_MCP_TOKEN") or None,
        )
        timeout_override = _env_float("BODHAN_TIMEOUT_S", 0)
        if timeout_override > 0:
            s.timeouts = dict.fromkeys(s.timeouts, timeout_override)
        return s

    def key_for(self, model: str) -> str:
        key = self.keys.get(model)
        if not key:
            var = MODEL_KEY_ENV.get(model, "BODHAN_API_KEY")
            raise errors.BodhanError(
                errors.MISSING_API_KEY,
                f"No API key configured for {model}.",
                hint=f"Set {var} (or BODHAN_API_KEY) in the server's environment. Keys come from https://console.bodhan.ai.",
            )
        return key
