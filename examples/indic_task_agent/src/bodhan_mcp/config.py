"""Settings: read from the environment (and .env), with safe defaults and clear errors.

Graders run this code with their own keys set in the four per-model variables, so those
names are fixed by the assignment. BODHAN_API_KEY is only a convenience fallback.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from bodhan_mcp.errors import ConfigError

DEFAULT_BASE_URL = "https://api.bodhan.ai"
MODELS = ("transcribe", "translate", "speak", "ocr")
KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}


def _clean(value: str | None) -> str:
    """Trim spaces and quotes. Treat unfilled '<placeholders>' from .env.example as empty."""
    v = (value or "").strip().strip("\"'").strip()
    return "" if v.startswith("<") else v


def normalize_base_url(url: str | None) -> str:
    """Accept 'https://api.bodhan.ai', '.../', or '.../v1' and return the bare host.

    The assignment text mentions '/v1' but /translate lives at the root, so we always
    strip '/v1' and add the route paths ourselves.
    """
    u = _clean(url) or DEFAULT_BASE_URL
    u = u.rstrip("/")
    if u.endswith("/v1"):
        u = u[: -len("/v1")]
    return u.rstrip("/")


@dataclass(frozen=True)
class Settings:
    base_url: str
    keys: Mapping[str, str] = field(default_factory=dict)  # model name -> key ("" if unset)
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    cache_dir: Path = Path(".cache/bodhan")

    def key_for(self, model: str) -> str:
        key = self.keys.get(model, "")
        if not key:
            raise ConfigError(
                f"No API key for '{model}'. Set {KEY_ENV[model]} (or BODHAN_API_KEY) in .env."
            )
        return key


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings. Pass `env` in tests; by default read .env and os.environ."""
    if env is None:
        load_dotenv()  # never overrides variables that are already set
        env = os.environ

    shared = _clean(env.get("BODHAN_API_KEY"))
    keys = {m: _clean(env.get(KEY_ENV[m])) or shared for m in MODELS}  # per-model wins
    return Settings(
        base_url=normalize_base_url(env.get("BODHAN_BASE_URL")),
        keys=keys,
        llm_base_url=_clean(env.get("LLM_BASE_URL")),
        llm_model=_clean(env.get("LLM_MODEL")),
        llm_api_key=_clean(env.get("LLM_API_KEY")),
        cache_dir=Path(_clean(env.get("BODHAN_CACHE_DIR")) or ".cache/bodhan"),
    )