"""OpenAI-compatible LLM client (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY). No provider-only features are used."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from bodhan_mcp import config


class ConfigError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLM(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply: ...


@dataclass
class LLMSettings:
    base_url: str
    model: str
    api_key: str

    @classmethod
    def from_env(cls) -> LLMSettings:
        config.load_env()
        missing = [k for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not os.environ.get(k, "").strip()]
        if missing:
            raise ConfigError(f"Missing LLM settings: {', '.join(missing)}. Set them in the environment or .env (see .env.example); "
                              "they point to any OpenAI-compatible chat-completions endpoint that supports tool calling.")
        return cls(os.environ["LLM_BASE_URL"].strip(), os.environ["LLM_MODEL"].strip(), os.environ["LLM_API_KEY"].strip())


class OpenAICompatLLM:
    def __init__(self, settings: LLMSettings, max_retries: int = 4):
        from openai import AsyncOpenAI

        self.settings = settings
        self.client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=60.0, max_retries=0)
        self.max_retries = max_retries

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp: Any = await self.client.chat.completions.create(model=self.settings.model, messages=messages, tools=tools, tool_choice="auto")
                msg = resp.choices[0].message
                calls = [ToolCall(tc.id, tc.function.name, tc.function.arguments or "{}") for tc in (msg.tool_calls or [])]
                usage = getattr(resp, "usage", None)
                return LLMReply(msg.content or "", calls, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0)
            except Exception as exc:  # network / rate limit / 5xx: back off and retry
                last = exc
                status = getattr(exc, "status_code", None)
                if status in (400, 401, 403, 404):
                    break
                await asyncio.sleep(min(2 ** (attempt + 1), 16))
        raise RuntimeError(f"LLM call failed: {type(last).__name__}: {str(last)[:200]}")
