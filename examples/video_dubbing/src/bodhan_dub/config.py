"""Runtime settings, read from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.bodhan.ai"


@dataclass(frozen=True)
class Settings:
    base_url: str
    transcribe_key: str
    translate_key: str
    speak_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    # Optional knobs, sent only when set; dropped automatically if the provider rejects them.
    llm_reasoning_effort: str = ""
    llm_max_tokens: int | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env

        def get(name: str) -> str:
            value = (env.get(name) or "").strip()
            # Treat untouched .env.example placeholders as unset.
            return "" if value.startswith("<") else value

        shared = get("BODHAN_API_KEY")
        base = get("BODHAN_BASE_URL") or DEFAULT_BASE_URL
        # The assignment quotes https://api.bodhan.ai/v1; our routes add /v1 themselves
        # (/translate has no /v1 prefix), so normalise to the bare host.
        base = base.rstrip("/").removesuffix("/v1")
        return cls(
            base_url=base,
            transcribe_key=get("BODHAN_TRANSCRIBE_KEY") or shared,
            translate_key=get("BODHAN_TRANSLATE_KEY") or shared,
            speak_key=get("BODHAN_SPEAK_KEY") or shared,
            llm_base_url=get("LLM_BASE_URL"),
            llm_model=get("LLM_MODEL"),
            llm_api_key=get("LLM_API_KEY"),
            llm_reasoning_effort=get("LLM_REASONING_EFFORT"),
            llm_max_tokens=int(get("LLM_MAX_TOKENS")) if get("LLM_MAX_TOKENS").isdigit() else None,
        )

    def missing(self) -> list[str]:
        """Names of required variables that are not set."""
        required = {
            "BODHAN_TRANSCRIBE_KEY": self.transcribe_key,
            "BODHAN_TRANSLATE_KEY": self.translate_key,
            "BODHAN_SPEAK_KEY": self.speak_key,
            "LLM_BASE_URL": self.llm_base_url,
            "LLM_MODEL": self.llm_model,
            "LLM_API_KEY": self.llm_api_key,
        }
        return [name for name, value in required.items() if not value]
