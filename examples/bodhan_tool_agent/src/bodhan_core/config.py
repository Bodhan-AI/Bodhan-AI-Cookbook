"""Environment configuration. The only secrets are the four Bodhan keys and the LLM key."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Model id -> environment variable holding its key.
MODEL_KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}
FALLBACK_KEY_ENV = "BODHAN_API_KEY"


@dataclass(frozen=True)
class Settings:
    bodhan_base_url: str = "https://api.bodhan.ai"
    bodhan_keys: dict[str, str] = field(default_factory=dict)
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    call_timeout_s: float = 120.0
    max_concurrency: int = 4
    max_retries: int = 3
    cache_path: str | None = ".cache/bodhan_responses.sqlite"
    # Hard cap on total Bodhan spend for one server process (None = unlimited). The agent's
    # --max-cost-inr is a separate, per-run budget.
    max_cost_inr: float | None = None

    def key_for(self, model: str) -> str | None:
        return self.bodhan_keys.get(model)


def load_settings(env_file: str | None = None) -> Settings:
    """Read settings from the environment (and a .env file if present). Never logs key values.

    Set BODHAN_NO_DOTENV=1 to ignore .env files entirely (the test suite does, so it can never pick up
    real keys).
    """
    if not os.environ.get("BODHAN_NO_DOTENV"):
        load_dotenv(env_file, override=False)
    fallback = os.environ.get(FALLBACK_KEY_ENV)
    keys = {}
    for model, var in MODEL_KEY_ENV.items():
        value = os.environ.get(var) or fallback
        if value:
            keys[model] = value
    return Settings(
        bodhan_base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
        bodhan_keys=keys,
        llm_base_url=os.environ.get("LLM_BASE_URL"),
        llm_model=os.environ.get("LLM_MODEL"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        call_timeout_s=float(os.environ.get("BODHAN_CALL_TIMEOUT_S", "120")),
        max_concurrency=int(os.environ.get("BODHAN_MAX_CONCURRENCY", "4")),
        max_retries=int(os.environ.get("BODHAN_MAX_RETRIES", "3")),
        # Set BODHAN_CACHE_PATH="" to disable caching.
        cache_path=os.environ.get("BODHAN_CACHE_PATH", ".cache/bodhan_responses.sqlite") or None,
        max_cost_inr=float(os.environ["BODHAN_MAX_COST_INR"]) if os.environ.get("BODHAN_MAX_COST_INR") else None,
    )
