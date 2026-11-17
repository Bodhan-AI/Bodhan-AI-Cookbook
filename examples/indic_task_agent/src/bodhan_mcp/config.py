"""Server settings, read once from the environment.

Keys: each model reads its own variable (BODHAN_TRANSCRIBE_KEY, ...) and falls back to
BODHAN_API_KEY, so both the assignment's per-model convention and the Cookbook's single
key work. Nothing here is ever logged or returned to a client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODEL_KEY_VARS = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}


def _clean(value: str | None) -> str | None:
    """Treat empty strings and `<placeholder>` values copied from .env.example as unset."""
    if not value:
        return None
    value = value.strip()
    if not value or value.startswith("<"):
        return None
    return value


@dataclass(frozen=True)
class Settings:
    base_url: str = "https://api.bodhan.ai"
    keys: dict[str, str | None] = field(default_factory=dict)
    http_timeout_s: float = 90.0
    ocr_timeout_s: float = 180.0
    max_retries: int = 4
    max_concurrency: int = 4
    cache_dir: Path = Path(".bodhan_cache")
    cache_enabled: bool = True
    output_dir: Path = Path("outputs/mcp")

    @classmethod
    def from_env(cls) -> Settings:
        fallback = _clean(os.environ.get("BODHAN_API_KEY"))
        keys = {model: _clean(os.environ.get(var)) or fallback for model, var in MODEL_KEY_VARS.items()}
        return cls(
            base_url=(os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/"),
            keys=keys,
            http_timeout_s=float(os.environ.get("BODHAN_HTTP_TIMEOUT_S", 90)),
            ocr_timeout_s=float(os.environ.get("BODHAN_OCR_TIMEOUT_S", 180)),
            max_retries=int(os.environ.get("BODHAN_MAX_RETRIES", 4)),
            max_concurrency=max(1, int(os.environ.get("BODHAN_MAX_CONCURRENCY", 4))),
            cache_dir=Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")).expanduser(),
            cache_enabled=os.environ.get("BODHAN_CACHE", "1") not in {"0", "false", "off"},
            output_dir=Path(os.environ.get("BODHAN_MCP_OUTPUT_DIR", "outputs/mcp")).expanduser(),
        )

    def key_for(self, model: str) -> str | None:
        return self.keys.get(model)
