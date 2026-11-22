"""OpenAI-compatible chat helper for the pipeline LLM brain.

Uses LLM_BASE_URL / LLM_API_KEY / LLM_MODEL (e.g. Gemini via an OpenAI gateway).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import requests

DEFAULT_TIMEOUT_S = 120
DEFAULT_MAX_RETRIES = 3


def get_llm_base_url() -> str:
    return os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def get_llm_model() -> str:
    return os.environ.get("LLM_MODEL", "gemini-3.5-flash")


def get_llm_api_key() -> str:
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise RuntimeError("Missing LLM_API_KEY in .env")
    return key


def normalize_usage(usage: Any) -> dict[str, int]:
    """Normalize provider usage into prompt/completion/total token counts."""
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens", 0)
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens", 0)
    total = usage.get("total_tokens")
    if total is None:
        total = int(prompt or 0) + int(completion or 0)

    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(total or 0),
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text (allows ```json fences)."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty LLM content")

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object")
    return data


def chat_completions(
    messages: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[str, dict[str, int]]:
    """POST /chat/completions; return (content, usage)."""
    key = api_key or get_llm_api_key()
    url = f"{get_llm_base_url()}/chat/completions"
    body = {
        "model": model or get_llm_model(),
        "messages": messages,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    last_error = "LLM request failed"
    for attempt in range(max_retries):
        response = requests.post(url, headers=headers, json=body, timeout=timeout_s)
        if response.ok:
            payload = response.json()
            choices = payload.get("choices") or []
            if not choices:
                raise RuntimeError("LLM response had no choices")
            content = choices[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise RuntimeError("LLM response missing message.content")
            return content, normalize_usage(payload.get("usage"))

        last_error = f"{response.status_code}: {response.text[:200]}"
        if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < max_retries:
            time.sleep(2**attempt)
            continue
        raise RuntimeError(last_error)

    raise RuntimeError(last_error)


def chat_json(
    messages: list[dict[str, Any]],
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Call the LLM and parse the reply as JSON. Returns (object, usage)."""
    text, usage = chat_completions(messages, api_key=api_key, model=model)
    return _extract_json_object(text), usage
