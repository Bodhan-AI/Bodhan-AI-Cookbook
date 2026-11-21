"""Runtime settings, read from the environment once.

Keys are per model (BODHAN_TRANSCRIBE_KEY, ...), falling back to BODHAN_API_KEY. Nothing here is ever logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODEL_KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}

# Hosted prices in rupees (assignment brief).
PRICE_TRANSCRIBE_PER_MIN = 0.10
PRICE_TRANSLATE_PER_10K_OUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_OCR_PER_IMAGE = 0.20


def _base_url() -> str:
    url = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


@dataclass
class Settings:
    base_url: str = field(default_factory=_base_url)
    timeout_s: float = field(default_factory=lambda: float(os.environ.get("BODHAN_TIMEOUT_S", "120")))
    max_parallel: int = field(default_factory=lambda: int(os.environ.get("BODHAN_MAX_PARALLEL", "4")))
    max_retries: int = 4
    cache_dir: Path = field(default_factory=lambda: Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache")))
    output_dir: Path = field(default_factory=lambda: Path(os.environ.get("BODHAN_MCP_OUTPUT_DIR", "outputs/mcp")))
    allowed_dirs: list[Path] = field(default_factory=list)
    max_cost_inr: float | None = field(
        default_factory=lambda: float(os.environ["BODHAN_MAX_COST_INR"]) if os.environ.get("BODHAN_MAX_COST_INR") else None
    )

    def __post_init__(self) -> None:
        if not self.allowed_dirs:
            extra = [Path(p) for p in os.environ.get("BODHAN_MCP_ALLOWED_DIRS", "").split(os.pathsep) if p]
            self.allowed_dirs = [Path.cwd(), *extra]

    def key_for(self, model: str) -> str | None:
        for name in (MODEL_KEY_ENV[model], "BODHAN_API_KEY"):
            value = (os.environ.get(name) or "").strip()
            if value and not value.startswith("<"):  # an untouched "<placeholder>" from .env.example counts as empty
                return value
        return None
