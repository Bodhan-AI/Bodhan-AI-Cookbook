"""Thin wrapper over any OpenAI-compatible chat endpoint with tool calling.

Reads LLM_BASE_URL, LLM_MODEL and LLM_API_KEY. Uses only the portable subset of the API:
`messages`, `tools`, `tool_choice` ("auto" or a named function). If a provider rejects a forced
tool choice, we fall back to "auto" plus an instruction, and finally to parsing JSON from text.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    parse_error: str | None = None


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    def as_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": c.raw_arguments or json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in self.tool_calls
            ]
        return msg


class ChatModel(Protocol):
    model: str

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], force_tool: str | None = None
    ) -> LLMReply: ...


class LLMConfigError(RuntimeError):
    pass


def _parse_args(raw: str) -> tuple[dict[str, Any], str | None]:
    if not raw:
        return {}, None
    try:
        val = json.loads(raw)
        return (val, None) if isinstance(val, dict) else ({}, "arguments must be a JSON object")
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON arguments: {exc}"


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, model: str, api_key: str, timeout_s: float = 120.0, max_retries: int = 3) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=max_retries)
        self.forced_choice_supported = True

    @classmethod
    def from_env(cls, timeout_s: float = 120.0) -> OpenAICompatibleLLM:
        missing = [
            v
            for v in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
            if not (os.environ.get(v) or "").strip() or os.environ[v].startswith("<")
        ]
        if missing:
            raise LLMConfigError(f"Missing LLM settings: {', '.join(missing)} (see .env.example).")
        return cls(os.environ["LLM_BASE_URL"], os.environ["LLM_MODEL"], os.environ["LLM_API_KEY"], timeout_s)

    async def _create_with_validation_retry(self, kwargs: dict[str, Any]) -> Any:
        """Some providers (Groq) validate the model's tool-call arguments and return 400 when the model
        produced an invalid call. Sampling again usually fixes it, so retry such errors twice."""
        from openai import BadRequestError

        for attempt in range(3):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except BadRequestError as exc:
                text = str(exc).lower()
                retryable = (
                    "tool call validation" in text or "tool_use_failed" in text or "failed to call a function" in text
                )
                if attempt < 2 and retryable:
                    continue
                raise
        raise RuntimeError("unreachable")

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], force_tool: str | None = None
    ) -> LLMReply:
        from openai import BadRequestError

        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = (
                {"type": "function", "function": {"name": force_tool}}
                if (force_tool and self.forced_choice_supported)
                else "auto"
            )
        try:
            resp = await self._create_with_validation_retry(kwargs)
        except BadRequestError:
            if not (force_tool and self.forced_choice_supported):
                raise
            # Provider does not support a named tool_choice: fall back to "auto" with an explicit instruction.
            self.forced_choice_supported = False
            kwargs["tool_choice"] = "auto"
            kwargs["messages"] = [
                *messages,
                {"role": "user", "content": f"Respond by calling the `{force_tool}` tool."},
            ]
            resp = await self._create_with_validation_retry(kwargs)
        choice = resp.choices[0].message
        calls = []
        for tc in choice.tool_calls or []:
            args, err = _parse_args(tc.function.arguments or "")
            calls.append(ToolCall(tc.id, tc.function.name, args, tc.function.arguments or "", err))
        content = choice.content or ""
        if force_tool and not calls and content:
            # Last resort for providers that answer in text: accept a JSON object in the content.
            m = re.search(r"\{.*\}", content, re.S)
            if m:
                args, err = _parse_args(m.group(0))
                if not err:
                    calls.append(ToolCall("text_fallback_0", force_tool, args, m.group(0)))
        usage = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens or 0,
                "completion_tokens": resp.usage.completion_tokens or 0,
            }
        return LLMReply(content, calls, usage)
