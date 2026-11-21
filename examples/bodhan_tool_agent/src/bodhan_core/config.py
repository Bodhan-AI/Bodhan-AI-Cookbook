"""Configuration: environment variables only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MODELS = ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr")

KEY_ENV = {
    "indic-transcribe": "BODHAN_TRANSCRIBE_KEY",
    "indic-translate": "BODHAN_TRANSLATE_KEY",
    "indic-speak": "BODHAN_SPEAK_KEY",
    "indic-ocr": "BODHAN_OCR_KEY",
}


def load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv()


@dataclass
class Limits:
    """Model limits the tools absorb so callers never have to."""

    audio_window_s: float = 25.0  # Indic-Transcribe / Indic-Speak are ~30 s models; stay under
    audio_snap_s: float = 4.0  # look back this far for a quiet point to cut at
    tts_max_chars: int = 220  # one or two sentences per Indic-Speak request
    translate_max_chars: int = 1200  # per Indic-Translate request
    ocr_max_pages: int = 40
    text_preview_chars: int = 600  # what tools return inline; the rest lives in a file


@dataclass
class Settings:
    base_url: str = "https://api.bodhan.ai"
    keys: dict[str, str] = field(default_factory=dict)
    max_concurrency: int = 4
    timeout_s: float = 90.0
    max_retries: int = 3
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv_if_present()
        shared = os.environ.get("BODHAN_API_KEY", "")
        keys = {m: os.environ.get(env, "") or shared for m, env in KEY_ENV.items()}
        return cls(
            base_url=os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
            keys=keys,
            max_concurrency=int(os.environ.get("BODHAN_MAX_CONCURRENCY", "4")),
            timeout_s=float(os.environ.get("BODHAN_TIMEOUT_S", "90")),
        )


def default_work_dir() -> Path:
    return Path(os.environ.get("BODHAN_WORK_DIR", "./bodhan_mcp_out")).resolve()
