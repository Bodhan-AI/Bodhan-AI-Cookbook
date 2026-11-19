from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    env_file = Path(".env")
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            if k and k not in os.environ:
                os.environ[k] = v


PRICE_TRANSCRIBE_INR_PER_MINUTE: float = 0.10
PRICE_TRANSLATE_INR_PER_10K_OUTPUT_TOKENS: float = 0.20
PRICE_SPEAK_INR_PER_10K_INPUT_CHARS: float = 6.00
PRICE_OCR_INR_PER_IMAGE: float = 0.20

MAX_ASR_WINDOW_SECONDS: float = 28.0
HARD_ASR_WINDOW_SECONDS: float = 30.0
MAX_TTS_CHUNK_CHARS: int = 320
MAX_TRANSLATE_CHUNK_CHARS: int = 8000
PREVIEW_MAX_CHARS: int = 1200


@dataclass
class BodhanConfig:
    bodhan_base_url: str
    transcribe_key: str
    translate_key: str
    speak_key: str
    ocr_key: str

    llm_base_url: str
    llm_model: str
    llm_api_key: str

    cache_dir: Path
    max_concurrency: int = 4
    request_timeout_seconds: float = 60.0
    max_retries: int = 3

    @classmethod
    def from_env(cls, cache_dir_override: Optional[Path] = None) -> "BodhanConfig":
        base_url = os.environ.get("BODHAN_BASE_URL", "https://api.bodhan.ai/v1").rstrip("/")
        fallback_key = os.environ.get("BODHAN_API_KEY", "")

        transcribe_key = os.environ.get("BODHAN_TRANSCRIBE_KEY", fallback_key)
        translate_key = os.environ.get("BODHAN_TRANSLATE_KEY", fallback_key)
        speak_key = os.environ.get("BODHAN_SPEAK_KEY", fallback_key)
        ocr_key = os.environ.get("BODHAN_OCR_KEY", fallback_key)

        llm_base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        llm_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        llm_api_key = os.environ.get("LLM_API_KEY", "")

        cache_path = cache_dir_override or Path(os.environ.get("BODHAN_CACHE_DIR", ".bodhan_cache"))
        max_concurrency = int(os.environ.get("BODHAN_MAX_CONCURRENCY", "4"))
        timeout = float(os.environ.get("BODHAN_TIMEOUT_SECONDS", "60.0"))

        return cls(
            bodhan_base_url=base_url,
            transcribe_key=transcribe_key,
            translate_key=translate_key,
            speak_key=speak_key,
            ocr_key=ocr_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
            cache_dir=cache_path,
            max_concurrency=max(1, max_concurrency),
            request_timeout_seconds=timeout,
        )

    def get_key_for_model(self, model_name: str) -> str:
        m = model_name.lower()
        if "transcribe" in m:
            return self.transcribe_key
        if "translate" in m:
            return self.translate_key
        if "speak" in m:
            return self.speak_key
        if "ocr" in m or "doc-parser" in m:
            return self.ocr_key
        return self.translate_key or self.transcribe_key or self.speak_key or self.ocr_key
