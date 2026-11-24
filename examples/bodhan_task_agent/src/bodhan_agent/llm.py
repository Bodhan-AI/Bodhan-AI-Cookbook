"""The agent's LLM: any OpenAI-compatible chat endpoint, read from LLM_BASE_URL / LLM_MODEL / LLM_API_KEY.

Portability matters because the evaluators run their own provider. The client therefore:

* retries rate limits, timeouts, connection and 5xx errors with exponential backoff;
* on a 400 for an optional parameter, drops it and retries (temperature → tool_choice →
  parallel_tool_calls), remembering what the provider rejected;
* if the provider rejects native tool calling altogether, switches to a JSON protocol: tool
  schemas go into the system prompt and the model replies with a JSON object of tool calls.

The canonical message history is always OpenAI tool-calling format; JSON mode converts it on
the way out, so the agent loop never knows which mode is active.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("bodhan_agent.llm")


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, exactly as the model produced it

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0
    finish_reason: str | None = None
    mode: str = "native"

    def to_message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": tc.arguments}}
                for tc in self.tool_calls
            ]
        return msg

    def to_dict(self) -> dict[str, Any]:
        return {"content": self.content, "tool_calls": [t.to_dict() for t in self.tool_calls], "usage": self.usage,
                "latency_ms": self.latency_ms, "finish_reason": self.finish_reason, "mode": self.mode}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMResponse:
        return cls(content=d.get("content") or "", tool_calls=[ToolCall(**t) for t in d.get("tool_calls") or []],
                   usage=d.get("usage") or {}, latency_ms=d.get("latency_ms") or 0,
                   finish_reason=d.get("finish_reason"), mode=d.get("mode") or "native")


class LLM(Protocol):
    model: str

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                   tool_choice: str | dict | None = None, timeout: float = 120.0) -> LLMResponse: ...


# --------------------------------------------------------------------------------------
# JSON-protocol fallback
# --------------------------------------------------------------------------------------

def _json_mode_messages(messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                        tool_choice: str | dict | None) -> list[dict[str, Any]]:
    specs = [{"name": t["function"]["name"], "description": t["function"].get("description", ""),
              "parameters": t["function"].get("parameters", {})} for t in tools]
    forced = tool_choice["function"]["name"] if isinstance(tool_choice, dict) else None
    protocol = (
        "\n\nTOOL PROTOCOL: you cannot call functions natively. To act, reply with ONLY a JSON object, no prose "
        'around it: {"thought": "<one sentence>", "tool_calls": [{"name": "<tool>", "arguments": {...}}]}. '
        "Several independent calls may go in the list. Available tools (JSON Schema):\n"
        + json.dumps(specs, ensure_ascii=False)
        + (f"\nYou MUST call `{forced}` now." if forced else "")
    )
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            out.append({"role": "system", "content": m["content"] + protocol})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"] or "{}")}
                     for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": json.dumps({"thought": m.get("content") or "", "tool_calls": calls},
                                                                   ensure_ascii=False)})
        elif m["role"] == "tool":
            out.append({"role": "user", "content": f"TOOL RESULT ({m.get('name', 'tool')}):\n{m['content']}"})
        else:
            out.append({"role": m["role"], "content": m.get("content") or ""})
    return out


def _extract_json_object(text: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start: i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def parse_json_mode_reply(text: str) -> tuple[str, list[ToolCall]]:
    obj = _extract_json_object(text or "")
    if not obj:
        return text or "", []
    raw_calls = obj.get("tool_calls")
    if raw_calls is None and "name" in obj:
        raw_calls = [obj]
    calls = []
    for c in raw_calls or []:
        if isinstance(c, dict) and c.get("name"):
            args = c.get("arguments", {})
            calls.append(ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=str(c["name"]),
                                  arguments=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)))
    return str(obj.get("thought") or ""), calls


# --------------------------------------------------------------------------------------
# OpenAI-compatible client
# --------------------------------------------------------------------------------------

class OpenAICompatibleLLM:
    def __init__(self, base_url: str, model: str, api_key: str, *, max_retries: int = 4, temperature: float = 0.1,
                 http_client: Any = None) -> None:
        from openai import AsyncOpenAI

        self.model = model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=0, http_client=http_client)
        self._max_retries = max_retries
        self._temperature = temperature
        self._dropped: set[str] = set()
        self._max_tokens = int(os.environ.get("LLM_MAX_TOKENS", "4096"))
        self.mode = os.environ.get("LLM_TOOL_MODE", "native")  # native | json

    @classmethod
    def from_env(cls) -> OpenAICompatibleLLM:
        base, model, key = (os.environ.get(k, "").strip() for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"))
        missing = [k for k, v in (("LLM_BASE_URL", base), ("LLM_MODEL", model)) if not v]
        if missing:
            raise LLMError(f"Missing LLM configuration: {', '.join(missing)}. Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY "
                           "(any OpenAI-compatible endpoint with tool calling).")
        return cls(base, model, key)

    def _kwargs(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: Any) -> dict[str, Any]:
        if self.mode == "json":
            kw: dict[str, Any] = {"messages": _json_mode_messages(messages, tools, tool_choice)}
        else:
            # Strict providers reject unknown keys on tool messages; `name` is only used by JSON mode.
            clean = [{k: v for k, v in m.items() if not (m["role"] == "tool" and k == "name")} for m in messages]
            kw = {"messages": clean, "tools": tools}
            if tool_choice is not None and "tool_choice" not in self._dropped:
                kw["tool_choice"] = tool_choice
            if "parallel_tool_calls" not in self._dropped:
                kw["parallel_tool_calls"] = True
        if "temperature" not in self._dropped:
            kw["temperature"] = self._temperature
        if "max_tokens" not in self._dropped:
            # Without a cap some gateways reserve the model's full output window and refuse low-credit keys.
            kw["max_tokens"] = self._max_tokens
        return kw

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                   tool_choice: str | dict | None = None, timeout: float = 120.0) -> LLMResponse:
        import openai

        attempt = 0
        while True:
            attempt += 1
            t0 = time.monotonic()
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model, timeout=timeout, **self._kwargs(messages, tools, tool_choice)
                )
            except openai.BadRequestError as exc:
                msg = str(exc).lower()
                droppable = [p for p in ("temperature", "max_tokens", "tool_choice", "parallel_tool_calls") if p not in self._dropped]
                named = next((p for p in droppable if p in msg), None)
                if named:
                    log.warning("LLM provider rejected %s; retrying without it", named)
                    self._dropped.add(named)
                    continue
                unsupported = any(w in msg for w in ("not support", "unsupported", "not allowed", "unrecognized",
                                                     "unknown parameter", "extra inputs", "not available"))
                if self.mode == "native" and unsupported and any(w in msg for w in ("tool", "function")):
                    log.warning("LLM provider rejected native tool calling; switching to JSON tool protocol")
                    self.mode = "json"
                    continue
                if droppable and attempt <= 3:  # unnamed 400: shed optional params one at a time
                    self._dropped.add(droppable[0])
                    continue
                raise LLMError(f"LLM rejected the request: {exc}") from exc
            except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError,
                    openai.InternalServerError) as exc:
                if attempt > self._max_retries:
                    raise LLMError(f"LLM unavailable after {attempt} attempts: {type(exc).__name__}: {exc}") from exc
                delay = min(20.0, 1.0 * 2 ** (attempt - 1)) * (0.5 + random.random())
                log.warning("LLM %s; retry %d in %.1fs", type(exc).__name__, attempt, delay)
                await asyncio.sleep(delay)
                continue
            except openai.APIStatusError as exc:
                raise LLMError(f"LLM error {exc.status_code}: {exc}") from exc
            latency = int((time.monotonic() - t0) * 1000)
            if not resp.choices:
                raise LLMError("LLM returned no choices")
            choice = resp.choices[0]
            m = choice.message
            usage = {}
            if resp.usage:
                usage = {"prompt_tokens": resp.usage.prompt_tokens or 0, "completion_tokens": resp.usage.completion_tokens or 0}
            if self.mode == "json":
                thought, calls = parse_json_mode_reply(m.content or "")
                return LLMResponse(thought, calls, usage, latency, choice.finish_reason, "json")
            calls = [ToolCall(id=tc.id or f"call_{uuid.uuid4().hex[:12]}", name=tc.function.name,
                              arguments=tc.function.arguments or "{}") for tc in (m.tool_calls or []) if tc.function]
            content = m.content or ""
            if not calls and content:  # some models emit tool calls as text even in native mode
                thought, text_calls = parse_json_mode_reply(content)
                if text_calls:
                    content, calls = thought, text_calls
            return LLMResponse(content, calls, usage, latency, choice.finish_reason, "native")


# --------------------------------------------------------------------------------------
# Scripted LLM: replay of recorded traces, and deterministic tests
# --------------------------------------------------------------------------------------

Policy = Callable[[list[dict[str, Any]], list[dict[str, Any]]], LLMResponse]


class ScriptedLLM:
    """Returns pre-recorded responses in order, or asks a policy function for each turn."""

    def __init__(self, responses: list[LLMResponse] | None = None, policy: Policy | None = None, model: str = "scripted") -> None:
        self.model = model
        self._responses = list(responses or [])
        self._policy = policy
        self.calls = 0

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *,
                   tool_choice: str | dict | None = None, timeout: float = 120.0) -> LLMResponse:
        self.calls += 1
        if self._policy is not None:
            return self._policy(messages, tools)
        if not self._responses:
            raise LLMError("replay exhausted: the trace has no more recorded LLM responses")
        return self._responses.pop(0)
