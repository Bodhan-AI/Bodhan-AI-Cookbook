"""OpenAI-compatible chat client for the agent's LLM, with a fallback for providers without tool calling.

#Plan
- Read LLM_BASE_URL, LLM_MODEL, LLM_API_KEY. Use only plain chat.completions with `tools`; no provider-only features.
- Retry transient failures with backoff.
- Some open-weights deployments reject `tools` or answer with a JSON tool call in plain text. In both cases we
  parse the call out of the text, so the agent still works; the mode switch is logged and reported.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("bodhan_agent.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, parsed by the agent so bad JSON becomes a tool error the model can fix


@dataclass
class LLMReply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(RuntimeError):
    pass


def parse_text_tool_calls(content: str, tool_names: set[str]) -> list[ToolCall]:
    """Find {"name": ..., "arguments": {...}} (or {"tool": ...}) objects written as text."""
    calls: list[ToolCall] = []
    for blob in _json_objects(content):
        name = blob.get("name") or blob.get("tool") or blob.get("function")
        args = blob.get("arguments", blob.get("parameters", blob.get("args", {})))
        if isinstance(name, dict):
            name, args = name.get("name"), name.get("arguments", args)
        if isinstance(name, str) and name in tool_names:
            calls.append(ToolCall(f"call_{uuid.uuid4().hex[:8]}", name, args if isinstance(args, str) else json.dumps(args)))
    return calls


def _json_objects(text: str) -> list[dict]:
    out, depth, start = [], 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start: i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
    return out


def extract_json(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    objs = _json_objects(text)
    return max(objs, key=lambda o: len(json.dumps(o))) if objs else None


class OpenAICompatLLM:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None, timeout_s: float = 120):
        from openai import AsyncOpenAI

        self.base_url = base_url or os.environ.get("LLM_BASE_URL")
        self.model = model or os.environ.get("LLM_MODEL")
        api_key = api_key or os.environ.get("LLM_API_KEY") or "not-needed"
        if not self.base_url or not self.model:
            raise LLMError("LLM_BASE_URL and LLM_MODEL must be set (any OpenAI-compatible endpoint with tool calling).")
        self._client = AsyncOpenAI(base_url=self.base_url, api_key=api_key, timeout=timeout_s, max_retries=0)
        self.text_tool_mode = False
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.1) -> LLMReply:
        names = {t["function"]["name"] for t in tools or []}
        if tools and self.text_tool_mode:
            messages = _as_text_tool_messages(messages, tools)
            tools = None
        last: Exception | None = None
        for attempt in range(6):
            try:
                kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": temperature}
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                resp = await self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # the SDK raises many types; classify by status where present
                last = exc
                status = getattr(exc, "status_code", None)
                text = str(exc).lower()
                if tools and status in (400, 404, 422) and ("tool" in text or "function" in text):
                    log.warning("provider rejected tools; switching to text tool-call mode")
                    self.text_tool_mode = True
                    return await self.chat(messages, tools, temperature)
                if status == 413:
                    raise LLMError(f"LLM request is larger than the provider allows per minute ({exc}). "
                                   "Use a model or plan with a higher tokens-per-minute limit.") from exc
                if status in (400, 401, 403, 404, 422):
                    raise LLMError(f"LLM request rejected ({status}): {exc}") from exc
                delay = _retry_delay(exc) if status == 429 else None
                wait = delay if delay is not None else min(20, 2 ** attempt)
                log.warning("llm retry", extra={"status": status, "attempt": attempt + 1, "wait_s": round(wait, 1)})
                await asyncio.sleep(wait)
        else:
            raise LLMError(f"LLM unavailable after retries: {last}")
        self.calls += 1
        usage = getattr(resp, "usage", None)
        reply = LLMReply(prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0, completion_tokens=getattr(usage, "completion_tokens", 0) or 0)
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens
        msg = resp.choices[0].message
        reply.content = msg.content or ""
        for tc in msg.tool_calls or []:
            reply.tool_calls.append(ToolCall(tc.id or f"call_{uuid.uuid4().hex[:8]}", tc.function.name, tc.function.arguments or "{}"))
        if not reply.tool_calls and names and reply.content:
            reply.tool_calls = parse_text_tool_calls(reply.content, names)
        return reply

    def usage(self) -> dict:
        return {"model": self.model, "calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "text_tool_mode": self.text_tool_mode}


def _retry_delay(exc: Exception) -> float | None:
    """Seconds a rate-limited provider asks us to wait: Retry-After header, or 'try again in 19.4s' in the message."""
    response = getattr(exc, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        if header:
            return min(65.0, float(header) + 0.5)
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(exc))
    if m:
        return min(65.0, int(m.group(1) or 0) * 60 + float(m.group(2)) + 0.5)
    return None


def _as_text_tool_messages(messages: list[dict], tools: list[dict]) -> list[dict]:
    """Fallback protocol: describe the tools in the system prompt and turn tool messages into user messages."""
    spec = "\n".join(f"- {t['function']['name']}: {t['function']['description'][:300]} Parameters: {json.dumps(t['function']['parameters'])[:800]}"
                     for t in tools)
    guide = ("\n\nTOOLS (call exactly one per reply by answering with only a JSON object "
             '{"name": "<tool>", "arguments": {...}}):\n' + spec)
    out: list[dict] = []
    names = {c["id"]: c["function"]["name"] for m in messages if m["role"] == "assistant" for c in m.get("tool_calls") or []}
    for m in messages:
        if m["role"] == "system":
            out.append({"role": "system", "content": m["content"] + guide})
        elif m["role"] == "tool":
            out.append({"role": "user", "content": f"Result of {names.get(m.get('tool_call_id'), 'tool')}:\n{m['content']}"})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"] or "{}")} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": json.dumps(calls[0] if len(calls) == 1 else calls)})
        else:
            out.append(m)
    return out
