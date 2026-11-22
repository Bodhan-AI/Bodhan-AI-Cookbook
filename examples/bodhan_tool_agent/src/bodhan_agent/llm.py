"""OpenAI-compatible chat client (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY) with retries."""
from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallReq:
    id: str
    name: str
    arguments: str


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCallReq] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(Exception):
    pass


class LLMToolCallError(LLMError):
    """The provider rejected the model's tool call as malformed (HTTP 400). Usually fixed by asking again."""


_BAD_TOOL_MARKERS = ("tool_use_failed", "failed to call a function", "tool call validation",
                     "failed_generation", "tool_call", "invalid_request_error")


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 http_client: Any = None):
        self.base_url = base_url or os.environ.get("LLM_BASE_URL")
        self.model = model or os.environ.get("LLM_MODEL")
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        missing = [n for n, v in (("LLM_BASE_URL", self.base_url), ("LLM_MODEL", self.model),
                                  ("LLM_API_KEY", self.api_key)) if not v]
        if missing:
            raise LLMError(f"Missing LLM settings: {', '.join(missing)}. Set them in .env (see .env.example).")
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=120, max_retries=0,
                                   http_client=http_client)

    async def aclose(self) -> None:
        await self._client.close()

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Reply:
        import openai
        retryable = (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
                     openai.InternalServerError)
        last: Exception | None = None
        for attempt in range(6):
            try:
                kw: dict[str, Any] = {"model": self.model, "messages": messages}  # no sampling params: some providers reject them
                if tools:
                    kw["tools"] = tools
                    kw["tool_choice"] = "auto"
                r = await self._client.chat.completions.create(**kw)
                m = r.choices[0].message
                calls = [ToolCallReq(c.id, c.function.name, c.function.arguments or "{}")
                         for c in (m.tool_calls or [])]
                u = getattr(r, "usage", None)
                return Reply(m.content or "", calls, getattr(u, "prompt_tokens", 0) or 0,
                             getattr(u, "completion_tokens", 0) or 0)
            except retryable as e:
                last = e
                wait = min(20, 2 ** attempt) * (0.7 + random.random() * 0.6)
                if isinstance(e, openai.RateLimitError):  # per-minute token limits: obey the provider's Retry-After
                    try:
                        wait = max(wait, min(60.0, float(e.response.headers.get("retry-after", "") or 0)) + 0.5)
                    except (ValueError, AttributeError):
                        wait = max(wait, 8.0)
                await asyncio.sleep(wait)
            except openai.APIStatusError as e:
                if e.status_code == 400 and any(m in str(e).lower() for m in _BAD_TOOL_MARKERS) \
                        and "model" not in str(e).lower()[:60]:
                    raise LLMToolCallError(f"LLM rejected a tool call (HTTP 400): {str(e)[:400]}")
                hint = ""
                if e.status_code == 404:
                    hint = (f" - model '{self.model}' was not found at {self.base_url}. Check LLM_MODEL "
                            "(run: python scripts/list_llm_models.py)")
                elif e.status_code in (401, 403):
                    hint = " - check LLM_API_KEY"
                elif e.status_code == 400 and "tool" in str(e).lower():
                    hint = " - this model may not support tool calling; pick another LLM_MODEL"
                raise LLMError(f"LLM request failed: HTTP {e.status_code}: {str(e)[:300]}{hint}")
        raise LLMError(f"LLM unreachable after retries: {last}")
