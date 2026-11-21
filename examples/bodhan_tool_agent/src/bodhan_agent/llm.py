"""Thin OpenAI-compatible chat client (tool calling only; no provider-specific features)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as sent by the model


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)


class LLM(Protocol):
    model: str

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply: ...


class LLMConfigError(RuntimeError):
    pass


class OpenAICompatLLM:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_s: float = 120.0):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=3)

    @classmethod
    def from_env(cls) -> "OpenAICompatLLM":
        missing = [k for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY") if not os.environ.get(k)]
        if missing:
            raise LLMConfigError(f"Missing environment variable(s): {', '.join(missing)}. See .env.example.")
        return cls(os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"], os.environ["LLM_API_KEY"])

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:
        resp = self._client.chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto", temperature=0)
        msg = resp.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in (msg.tool_calls or [])]
        usage = getattr(resp, "usage", None)
        tokens = {"prompt": getattr(usage, "prompt_tokens", 0) or 0, "completion": getattr(usage, "completion_tokens", 0) or 0} if usage else {}
        return LLMReply(content=msg.content or "", tool_calls=calls, tokens=tokens)
