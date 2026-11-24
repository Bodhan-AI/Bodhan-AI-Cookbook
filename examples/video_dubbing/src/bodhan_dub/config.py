"""Read API keys from the environment / .env. Never logs key values."""

from __future__ import annotations

import os
from pathlib import Path

BASE_URL = "https://api.bodhan.ai"

# Required names first (per the assignment), then names used in the local .env, then the Cookbook's single
# BODHAN_API_KEY (one key for every model) as the last fallback.
KEY_NAMES = {
    "transcribe": ["BODHAN_TRANSCRIBE_KEY", "Indic_transcribe_api_key", "BODHAN_API_KEY"],
    "translate": ["BODHAN_TRANSLATE_KEY", "Indic_translate_api_key", "BODHAN_API_KEY"],
    "speak": ["BODHAN_SPEAK_KEY", "Indic_speech_api_key", "BODHAN_API_KEY"],
    "ocr": ["BODHAN_OCR_KEY", "BODHAN_API_KEY"],
}


def _find_dotenv() -> Path | None:
    for d in [Path.cwd(), *Path.cwd().parents]:
        if (d / ".env").is_file():
            return d / ".env"
    return None


def load_env() -> dict[str, str]:
    """Merge .env (if found) with os.environ; real environment variables win."""
    env: dict[str, str] = {}
    path = _find_dotenv()
    if path:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update(os.environ)
    return env


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"


def get_llm_config() -> dict[str, str]:
    """OpenAI-compatible LLM settings: LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.

    Local convenience: if only GROQ_API_KEY is set, use Groq with its default model.
    """
    env = {k.lower(): v for k, v in load_env().items()}
    if env.get("llm_api_key"):
        if not (env.get("llm_base_url") and env.get("llm_model")):
            raise RuntimeError("LLM_API_KEY is set but LLM_BASE_URL / LLM_MODEL are missing")
        return {"base_url": env["llm_base_url"].rstrip("/"), "model": env["llm_model"], "key": env["llm_api_key"]}
    if env.get("groq_api_key"):
        return {"base_url": env.get("llm_base_url", GROQ_BASE_URL).rstrip("/"),
                "model": env.get("llm_model", GROQ_DEFAULT_MODEL), "key": env["groq_api_key"]}
    raise RuntimeError("No LLM configured. Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY in .env")


def get_key(service: str) -> str:
    """service: 'transcribe' | 'translate' | 'speak' | 'ocr'."""
    env = {k.lower(): v for k, v in load_env().items()}
    for name in KEY_NAMES[service]:
        if env.get(name.lower()):
            return env[name.lower()]
    raise RuntimeError(f"Missing API key for '{service}'. Set {KEY_NAMES[service][0]} in .env")
