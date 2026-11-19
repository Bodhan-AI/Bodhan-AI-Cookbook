"""Environment configuration. Only the seven variables named in the assignment are
secrets; everything else is a CLI flag with a safe default."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BODHAN_BASE_URL = "https://api.bodhan.ai"

MODEL_TRANSCRIBE = "indic-transcribe"
MODEL_TRANSLATE = "indic-translate"
MODEL_SPEAK = "indic-speak"
MODEL_OCR = "indic-ocr"

KEY_ENV = {
    MODEL_TRANSCRIBE: "BODHAN_TRANSCRIBE_KEY",
    MODEL_TRANSLATE: "BODHAN_TRANSLATE_KEY",
    MODEL_SPEAK: "BODHAN_SPEAK_KEY",
    MODEL_OCR: "BODHAN_OCR_KEY",
}
ALLOWED_ENV = (*KEY_ENV.values(), "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")


def load_env_file(explicit: str | None = None) -> None:
    """Load the allowed variables from a .env file (cwd, or `explicit`) without
    overriding values already in the environment. Other keys in the file are ignored."""
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - dependency is pinned
        return
    path = Path(explicit) if explicit else Path.cwd() / ".env"
    if not path.is_file():
        return
    for key, value in dotenv_values(path).items():
        if key in ALLOWED_ENV and value and not os.environ.get(key):
            os.environ[key] = value


@dataclass(frozen=True)
class BodhanKeys:
    transcribe: str | None
    translate: str | None
    speak: str | None
    ocr: str | None

    @classmethod
    def from_env(cls) -> BodhanKeys:
        get = lambda name: (os.environ.get(name) or "").strip() or None  # noqa: E731
        return cls(get("BODHAN_TRANSCRIBE_KEY"), get("BODHAN_TRANSLATE_KEY"), get("BODHAN_SPEAK_KEY"), get("BODHAN_OCR_KEY"))

    def for_model(self, model: str) -> str | None:
        return {MODEL_TRANSCRIBE: self.transcribe, MODEL_TRANSLATE: self.translate,
                MODEL_SPEAK: self.speak, MODEL_OCR: self.ocr}[model]

    def availability(self) -> dict[str, bool]:
        return {m: bool(self.for_model(m)) for m in KEY_ENV}
