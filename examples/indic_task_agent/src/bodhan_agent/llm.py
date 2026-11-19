"""Portable OpenAI-compatible chat client for the planner.

Uses only `chat.completions` with `tools` + `tool_choice="auto"`, the common
subset that OpenAI-compatible providers implement. If a provider answers with
a JSON tool call in plain text instead of `tool_calls`, we parse that as a
fallback so less capable endpoints still work.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    parse_error: str | None = None


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    finish_reason: str | None
    assistant_message: dict[str, Any] = field(default_factory=dict)
    fallback_parsed: bool = False


class LLMError(Exception):
    pass


class LLMConfigError(LLMError):
    pass


def llm_settings() -> tuple[str, str, str]:
    base, model, key = (os.environ.get(k, "").strip() for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"))
    missing = [k for k, v in (("LLM_BASE_URL", base), ("LLM_MODEL", model), ("LLM_API_KEY", key)) if not v]
    if missing:
        raise LLMConfigError(f"Missing LLM settings: {', '.join(missing)}. Set them in the environment or .env.")
    return base, model, key


_JSON_CALL = re.compile(r"\{[\s\S]*\}")


def _fallback_tool_call(content: str, known: set[str]) -> ToolCall | None:
    """Accept {"name": "...", "arguments": {...}} (or "tool"/"function") written as text."""
    for match in _JSON_CALL.finditer(content or ""):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
        name = obj.get("name") or obj.get("tool") or fn.get("name")
        args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name in known and isinstance(args, dict):
            return ToolCall(f"fallback_{uuid.uuid4().hex[:8]}", name, args, json.dumps(args))
    return None


class PlannerLLM:
    def __init__(self, *, timeout: float = 120.0, max_attempts: int = 3, client: Any = None, model: str | None = None):
        if client is None:
            from openai import AsyncOpenAI

            base, model_env, key = llm_settings()
            client = AsyncOpenAI(base_url=base, api_key=key, timeout=timeout, max_retries=0)
            model = model or model_env
        self.client = client
        self.model = model or "test-model"
        self.max_attempts = max_attempts
        self._no_max_tokens = False

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int | None = None) -> LLMReply:
        """`max_tokens` is only sent when the planner is priced (to bound spend). Some providers reject it
        (they expect max_completion_tokens); then it is dropped and the call is repeated without it."""
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            t0 = time.monotonic()
            try:
                extra = {"max_tokens": max_tokens} if max_tokens and not self._no_max_tokens else {}
                try:
                    resp = await self.client.chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto", **extra)
                except Exception as exc:
                    if extra and getattr(exc, "status_code", None) == 400 and "max_tokens" in str(exc):
                        self._no_max_tokens = True
                        resp = await self.client.chat.completions.create(model=self.model, messages=messages, tools=tools, tool_choice="auto")
                    else:
                        raise
                return self._parse(resp, time.monotonic() - t0, {t["function"]["name"] for t in tools})
            except Exception as exc:  # network, 429, 5xx, malformed
                last_exc = exc
                status = getattr(exc, "status_code", None)
                if status in (400, 401, 403, 404, 422) and attempt == 1 and "tool" not in str(exc).lower():
                    break  # configuration error; retrying will not help
                if attempt < self.max_attempts:
                    await asyncio.sleep(2 ** attempt)
        raise LLMError(f"LLM call failed after retries: {type(last_exc).__name__}: {str(last_exc)[:300]}")

    def _parse(self, resp: Any, latency: float, known: set[str]) -> LLMReply:
        choice = resp.choices[0]
        msg = choice.message
        usage = getattr(resp, "usage", None)
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw) if raw.strip() else {}
                err = None if isinstance(args, dict) else "arguments are not a JSON object"
            except json.JSONDecodeError as exc:
                args, err = {}, f"arguments are not valid JSON: {exc}"
            calls.append(ToolCall(tc.id or f"call_{uuid.uuid4().hex[:8]}", tc.function.name, args if isinstance(args, dict) else {}, raw, err))
        content = msg.content or ""
        fallback = False
        if not calls and content:
            fb = _fallback_tool_call(content, known)
            if fb:
                calls, fallback = [fb], True
        assistant: dict[str, Any] = {"role": "assistant", "content": content}
        if calls:
            assistant["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]
            if fallback:
                assistant["content"] = ""
        return LLMReply(content, calls, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0,
                        latency, choice.finish_reason, assistant, fallback)
