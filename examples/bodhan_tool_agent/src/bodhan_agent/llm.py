"""OpenAI-compatible chat client for the agent's planner.

Configured only from LLM_BASE_URL, LLM_MODEL and LLM_API_KEY. Uses plain chat.completions with
function tools (no provider-only features). Fallbacks:
  * transient errors (timeouts, 429, 5xx): retried with backoff
  * a provider that rejects ``temperature``: retried without it
  * a provider/model without native tool calling: tools are described in the prompt and the model
    replies with a JSON object, which is parsed into the same tool-call structure
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    mode: str = "native"


@dataclass
class LLMConfig:
    base_url: str | None
    model: str | None
    api_key: str | None
    timeout_s: float = 120.0
    temperature: float | None = 0.2
    price_inr_per_1m_input: float = 0.0
    price_inr_per_1m_output: float = 0.0

    @classmethod
    def from_env(cls) -> LLMConfig:
        def f(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except ValueError:
                return default

        return cls(
            base_url=os.environ.get("LLM_BASE_URL") or None,
            model=os.environ.get("LLM_MODEL") or None,
            api_key=os.environ.get("LLM_API_KEY") or None,
            timeout_s=f("LLM_TIMEOUT_S", 120.0),
            price_inr_per_1m_input=f("LLM_PRICE_INR_PER_1M_INPUT", 0.0),
            price_inr_per_1m_output=f("LLM_PRICE_INR_PER_1M_OUTPUT", 0.0),
        )

    def missing(self) -> list[str]:
        return [n for n, v in (("LLM_BASE_URL", self.base_url), ("LLM_MODEL", self.model), ("LLM_API_KEY", self.api_key)) if not v]


class LLMError(RuntimeError):
    pass


TEXT_PROTOCOL = """\
Your provider does not support native tool calling, so call tools by replying with ONLY a JSON object:
{"tool_calls": [{"name": "<tool name>", "arguments": {...}}]}
You may put several calls in the list when they are independent. Available tools (JSON Schema):
"""


def _extract_json_calls(text: str) -> list[ToolCall]:
    """Parse tool calls a model wrote as JSON text (fallback protocol, or models that ignore native tools)."""
    if not text or "{" not in text:
        return []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = fenced + [text[text.find("{"): text.rfind("}") + 1]]
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        calls = obj.get("tool_calls") if isinstance(obj, dict) else None
        if calls is None and isinstance(obj, dict) and "name" in obj and "arguments" in obj:
            calls = [obj]
        if isinstance(calls, list):
            out = []
            for c in calls:
                if isinstance(c, dict) and isinstance(c.get("name"), str):
                    args = c.get("arguments", {})
                    out.append(ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=c["name"],
                                        arguments=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)))
            if out:
                return out
    return []


@dataclass
class LLM:
    cfg: LLMConfig
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    native_tools: bool = True
    use_temperature: bool = True
    _client: Any = field(default=None, repr=False)

    def _get(self):
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(base_url=self.cfg.base_url, api_key=self.cfg.api_key, timeout=self.cfg.timeout_s, max_retries=0)
        return self._client

    def cost_inr(self) -> float:
        return (self.prompt_tokens * self.cfg.price_inr_per_1m_input + self.completion_tokens * self.cfg.price_inr_per_1m_output) / 1e6

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, max_attempts: int = 4) -> LLMReply:
        import openai

        last: Exception | None = None
        for attempt in range(max_attempts):
            kwargs: dict[str, Any] = {"model": self.cfg.model, "messages": messages}
            if self.use_temperature and self.cfg.temperature is not None:
                kwargs["temperature"] = self.cfg.temperature
            if tools:
                if self.native_tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                else:
                    schema = json.dumps([t["function"] for t in tools], ensure_ascii=False)
                    system_text = "\n\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
                    kwargs["messages"] = [
                        {"role": "system", "content": system_text + "\n\n" + TEXT_PROTOCOL + schema},
                        *(_flatten_tool_messages(m) for m in messages if m.get("role") != "system"),
                    ]
            try:
                resp = await self._get().chat.completions.create(**kwargs)
            except openai.BadRequestError as exc:
                msg = str(exc).lower()
                if "temperature" in msg and self.use_temperature:
                    self.use_temperature = False
                    continue
                if tools and self.native_tools:
                    # Some compatible gateways return an opaque 400 for native tool history.
                    # Replay the same conversation once with the portable text protocol.
                    self.native_tools = False
                    continue
                raise LLMError("LLM rejected the request (HTTP 400)") from exc
            except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError) as exc:
                last = exc
                await asyncio.sleep(min(30.0, 2.0 * (2 ** attempt)) * (0.5 + random.random() / 2))
                continue
            except openai.APIStatusError as exc:
                if exc.status_code >= 500 or exc.status_code == 429:
                    last = exc
                    await asyncio.sleep(min(30.0, 2.0 * (2 ** attempt)))
                    continue
                raise LLMError(f"LLM error {exc.status_code}: {exc}") from exc
            self.calls += 1
            usage = getattr(resp, "usage", None)
            pt = int(getattr(usage, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage, "completion_tokens", 0) or 0)
            self.prompt_tokens += pt
            self.completion_tokens += ct
            if not resp.choices:
                last = LLMError("LLM returned no choices")
                continue
            msg = resp.choices[0].message
            content = msg.content or ""
            calls = [ToolCall(id=tc.id or f"call_{uuid.uuid4().hex[:12]}", name=tc.function.name, arguments=tc.function.arguments or "{}")
                     for tc in (msg.tool_calls or []) if getattr(tc, "function", None)]
            mode = "native" if self.native_tools else "json_text"
            if tools and not calls:
                calls = _extract_json_calls(content)
                if calls:
                    mode = "json_text"
            return LLMReply(content=content, tool_calls=calls, prompt_tokens=pt, completion_tokens=ct, mode=mode)
        raise LLMError(f"LLM unavailable after {max_attempts} attempts: {last}")


def _flatten_tool_messages(m: dict) -> dict:
    """For the JSON-text fallback, turn tool-call history into plain messages any chat model accepts."""
    if m.get("role") == "tool":
        return {"role": "user", "content": f"[tool result for {m.get('tool_call_id')}]\n{m.get('content', '')}"}
    if m.get("role") == "assistant" and m.get("tool_calls"):
        calls = [{"name": c["function"]["name"], "arguments": c["function"]["arguments"]} for c in m["tool_calls"]]
        return {"role": "assistant", "content": (m.get("content") or "") + "\n" + json.dumps({"tool_calls": calls}, ensure_ascii=False)}
    return m


def parse_json_object(text: str) -> dict | None:
    """Best-effort extraction of one JSON object from model text (used for the plan)."""
    if not text:
        return None
    for cand in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL) + [text[text.find("{"): text.rfind("}") + 1]]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None
