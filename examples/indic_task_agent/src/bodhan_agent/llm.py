"""The one LLM client: OpenAI-compatible chat/completions, JSON out, no provider-only features."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from openai import OpenAI

from bodhan_mcp.core.errors import BodhanError


@dataclass
class LlmReply:
    text: str
    finish_reason: str | None
    prompt_chars: int
    latency_ms: int


def _client() -> OpenAI:
    base = os.environ.get("LLM_BASE_URL")
    model_key = os.environ.get("LLM_API_KEY")
    if not base or not model_key:
        raise BodhanError("LLM_UNAVAILABLE", "LLM_BASE_URL and LLM_API_KEY are not set.", hint="Point them at any OpenAI-compatible endpoint.")
    return OpenAI(base_url=base, api_key=model_key, max_retries=0, timeout=float(os.environ.get("LLM_TIMEOUT_S", "120")))


def model_name() -> str:
    return os.environ.get("LLM_MODEL", "default")


def chat(messages: list[dict[str, str]], max_tokens: int = 4096, temperature: float = 0.2) -> LlmReply:
    """One completion. 503 (queued capacity) and 429 are retried here; anything else is raised."""
    started = time.monotonic()
    last: Exception | None = None
    for attempt in range(4):
        try:
            resp = _client().chat.completions.create(model=model_name(), messages=messages, max_tokens=max(max_tokens, 1000), temperature=temperature)  # the endpoint 500s on tiny ceilings; reasoning needs room
            choice = resp.choices[0]
            text = choice.message.content or ""
            return LlmReply(
                text=text,
                finish_reason=getattr(choice, "finish_reason", None),
                prompt_chars=sum(len(m["content"]) for m in messages),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # openai raises typed errors; status is the portable signal
            status = getattr(exc, "status_code", None)
            last = exc
            if status in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(min(2**attempt, 8))
                continue
            break
    raise BodhanError("LLM_UNAVAILABLE", f"The planner endpoint failed: {type(last).__name__}.", hint="Check LLM_BASE_URL, LLM_MODEL and LLM_API_KEY.")
