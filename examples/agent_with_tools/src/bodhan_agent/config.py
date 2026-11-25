"""LLM settings, read from the environment (OpenAI-compatible endpoint with tool calling)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    api_key: str
    model: str


def load_llm_settings() -> LLMSettings:
    base_url = (os.environ.get("LLM_BASE_URL") or "").strip()
    api_key = (os.environ.get("LLM_API_KEY") or "").strip()
    model = (os.environ.get("LLM_MODEL") or "").strip()
    missing = [n for n, v in (("LLM_BASE_URL", base_url), ("LLM_API_KEY", api_key), ("LLM_MODEL", model)) if not v]
    if missing:
        raise SystemExit(f"{', '.join(missing)} must be set (see .env.example).")
    return LLMSettings(base_url=base_url, api_key=api_key, model=model)
