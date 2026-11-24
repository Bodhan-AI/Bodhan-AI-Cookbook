from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import ToolError


@dataclass(frozen=True)
class Settings:
    transcribe_key: str
    translate_key: str
    speak_key: str
    ocr_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    api_base_url: str = "https://api.bodhan.ai"

    @classmethod
    def from_env(cls, *, require_llm: bool = False, required_models: tuple[str, ...] = ()) -> "Settings":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        required = {
            "BODHAN_TRANSCRIBE_KEY": "transcribe_key",
            "BODHAN_TRANSLATE_KEY": "translate_key",
            "BODHAN_SPEAK_KEY": "speak_key",
            "BODHAN_OCR_KEY": "ocr_key",
        }
        vals: dict[str, str] = {}
        for env_name, field in required.items():
            value = os.getenv(env_name, "").strip()
            short_name = field.removesuffix("_key")
            if not value and short_name in required_models:
                raise ToolError(f"Set {env_name} in the environment before using this tool.", "missing_key")
            vals[field] = value
        llm_base = os.getenv("LLM_BASE_URL", "").strip()
        llm_model = os.getenv("LLM_MODEL", "").strip()
        llm_key = os.getenv("LLM_API_KEY", "").strip()
        if require_llm and not all((llm_base, llm_model, llm_key)):
            raise ToolError("Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY to run the task agent.", "missing_llm_config")
        return cls(
            **vals,
            llm_base_url=llm_base,
            llm_model=llm_model,
            llm_api_key=llm_key,
            api_base_url=os.getenv("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
        )
