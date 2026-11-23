"""Run configuration: CLI arguments plus settings read from the environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BODHAN_BASE_URL = "https://api.bodhan.ai/v1"

# Bodhan issues one key per model.
BODHAN_KEY_ENV = {
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
}


@dataclass(frozen=True)
class Config:
    dataset: Path
    langs: tuple[str, ...]
    out: Path
    generate: bool = False
    baseline: Path | None = None
    calibration: tuple[Path, ...] = ()  # labels files
    thresholds: Path | None = None  # None = thresholds.default.json
    max_cost_inr: float | None = None  # None = no cap
    skip_speech: bool = False
    max_speech_items: int | None = 30  # items to voice with --generate; None = as many as the budget allows
    log_level: str = "INFO"
    bodhan_base_url: str = DEFAULT_BODHAN_BASE_URL
    bodhan_keys: dict[str, str] = field(default_factory=dict, repr=False)
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = field(default=None, repr=False)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    def has_key(self, model: str) -> bool:
        return bool(self.bodhan_keys.get(model))

    def warnings(self) -> list[str]:
        """Stages that will be skipped because a credential is missing."""
        out = []
        if not self.has_key("translate"):
            out.append("BODHAN_TRANSLATE_KEY not set: back-translation checks and translation generation are off")
        if not self.has_key("transcribe"):
            out.append("BODHAN_TRANSCRIBE_KEY not set: speech back-transcription checks are off")
        if self.generate and not self.skip_speech and not self.has_key("speak"):
            out.append("BODHAN_SPEAK_KEY not set: --generate will not create audio")
        if not self.llm_enabled:
            out.append("LLM_BASE_URL/LLM_MODEL/LLM_API_KEY not all set: LLM judge is off")
        return out


def env_settings(env: Mapping[str, str]) -> dict:
    """Config fields that come from the environment. Empty values and <placeholders> count as unset."""
    env = {k: v for k, v in env.items() if v and not v.strip().startswith("<")}
    return {
        "bodhan_base_url": env.get("BODHAN_BASE_URL") or DEFAULT_BODHAN_BASE_URL,
        # One key per model, as the assignment specifies; BODHAN_API_KEY (the Cookbook's name) covers any not set.
        "bodhan_keys": {m: env.get(v) or env["BODHAN_API_KEY"] for m, v in BODHAN_KEY_ENV.items()
                        if env.get(v) or env.get("BODHAN_API_KEY")},
        "llm_base_url": env.get("LLM_BASE_URL") or None,
        "llm_model": env.get("LLM_MODEL") or None,
        "llm_api_key": env.get("LLM_API_KEY") or None,
    }


def load_dotenv(path: Path = Path(".env")) -> None:
    """Fill unset environment variables from a local .env file. Real env vars always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
