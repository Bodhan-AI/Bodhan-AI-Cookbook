"""Environment settings, prices and tunable thresholds."""

from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Category

DEFAULT_BASE_URL = "https://api.bodhan.ai"

# Hosted prices from the assignment brief (INR).
PRICE_TRANSLATE_PER_10K_OUTPUT_TOKENS = 0.20
PRICE_SPEAK_PER_10K_CHARS = 6.0
PRICE_TRANSCRIBE_PER_MINUTE = 0.10


def load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependency is pinned
        return
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


class Settings(BaseModel):
    offline: bool = False
    base_url: str = DEFAULT_BASE_URL
    translate_key: str | None = None
    speak_key: str | None = None
    transcribe_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_reasoning_effort: str | None = None
    timeout_s: float = 90.0
    max_attempts: int = 5
    concurrency: dict[str, int] = Field(default_factory=lambda: {"translate": 8, "speak": 4, "transcribe": 4, "llm": 12})

    @classmethod
    def from_env(cls) -> Settings:
        shared = os.environ.get("BODHAN_API_KEY") or None

        def key(name: str) -> str | None:
            value = os.environ.get(name) or shared
            return value if value and not value.startswith("<") else None

        llm_key = os.environ.get("LLM_API_KEY") or None
        return cls(
            base_url=(os.environ.get("BODHAN_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            translate_key=key("BODHAN_TRANSLATE_KEY"),
            speak_key=key("BODHAN_SPEAK_KEY"),
            transcribe_key=key("BODHAN_TRANSCRIBE_KEY"),
            llm_base_url=(os.environ.get("LLM_BASE_URL") or "").rstrip("/") or None,
            llm_model=os.environ.get("LLM_MODEL") or None,
            llm_api_key=llm_key if llm_key and not llm_key.startswith("<") else None,
            llm_reasoning_effort=os.environ.get("LLM_REASONING_EFFORT") or None,
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)


class Thresholds(BaseModel):
    """Decision thresholds. Defaults ship in default_thresholds.json and are re-tuned by --calibration."""

    flag: dict[str, float] = Field(default_factory=lambda: {c.value: 0.5 for c in Category})
    latin_run_words: int = 4
    foreign_script_ratio: float = 0.15
    expected_script_min_ratio: float = 0.5
    length_z: float = 3.0
    backtranslation_clause_coverage: float = 0.25
    min_internal_silence_s: float = 1.2
    clipped_fraction: float = 0.002
    min_noise_s: float = 0.3
    noise_flatness: float = 0.45
    duration_ratio_low: float = 0.6
    duration_ratio_high: float = 1.7
    asr_omission_min_words: int = 3
    asr_garbled_window: int = 4
    judge_reject_factor: float = 0.35
    judge_confirm_bonus: float = 0.25

    def threshold(self, category: Category | str) -> float:
        return self.flag.get(str(category), 0.5)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Thresholds:
        if path:
            return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        text = resources.files("bodhan_eval").joinpath("default_thresholds.json").read_text(encoding="utf-8")
        return cls.model_validate(json.loads(text))
