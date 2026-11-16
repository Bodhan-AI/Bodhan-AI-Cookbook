"""OpenAI-compatible chat client with tool calling, retries and fallbacks.

Only the plain chat-completions surface is used (messages, tools, temperature),
so any OpenAI-compatible endpoint works. Two degradations are handled:
- a provider that rejects `temperature` is retried without it;
- a provider that rejects `tools`, or answers with a JSON tool call in the
  message text instead of `tool_calls`, is driven through a text protocol
  where the call is parsed out of the content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import openai
from openai import AsyncOpenAI

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None
    # True when the call was recovered from message text rather than native tool_calls.
    from_text: bool = False
    # Provider-specific fields on the call (e.g. Gemini's thought_signature) that must be echoed back verbatim.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None


class ChatModel(Protocol):
    model: str

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:16]}"


# --------------------------------------------------------------------------
# Tool calls written as JSON in the message text
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)
_NAME_KEYS = ("tool", "name", "tool_name", "function")
_ARG_KEYS = ("arguments", "args", "parameters", "input")


def _balanced_json_spans(text: str) -> list[str]:
    """Top-level {...} / [...] spans, skipping brackets inside JSON strings."""
    spans: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch in "{[":
            if depth == 0:
                start = i
            depth += 1
        elif ch in "}]" and depth > 0:
            depth -= 1
            if depth == 0:
                spans.append(text[start : i + 1])
    return spans


def _as_call(obj: Any, known: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict):  # OpenAI-shaped {"type": "function", "function": {...}}
        obj = obj["function"]
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if not name or name not in known:
        return None
    raw_args: Any = next((obj[k] for k in _ARG_KEYS if k in obj), {})
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            return ToolCall(new_call_id(), name, {}, parse_error="arguments were not valid JSON", from_text=True)
    if not isinstance(raw_args, dict):
        return ToolCall(new_call_id(), name, {}, parse_error="arguments must be a JSON object", from_text=True)
    return ToolCall(new_call_id(), name, raw_args, from_text=True)


def _calls_from_obj(obj: Any, known: set[str]) -> list[ToolCall]:
    if isinstance(obj, list):
        return [c for item in obj for c in _calls_from_obj(item, known)]
    if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
        return _calls_from_obj(obj["tool_calls"], known)
    call = _as_call(obj, known)
    return [call] if call else []


def parse_tool_calls_from_text(text: str, known_tools: set[str]) -> list[ToolCall]:
    """Recover tool calls a model wrote as JSON in its reply (fenced or bare)."""
    if not text or "{" not in text:
        return []
    candidates = [m.group(1) for m in _FENCE.finditer(text)]
    candidates.extend(_balanced_json_spans(text))
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for chunk in candidates:
        chunk = chunk.strip()
        if chunk in seen:
            continue
        seen.add(chunk)
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        calls.extend(_calls_from_obj(obj, known_tools))
        if calls:
            # The first block that parses into calls is the answer; later spans are usually nested copies.
            break
    return calls


# --------------------------------------------------------------------------
# Text protocol for providers without native tool calling
# --------------------------------------------------------------------------

TEXT_PROTOCOL_HEADER = (
    "\n\n## Calling tools\nThis endpoint has no native tool calling. To call tools, reply with a single "
    'JSON array in a ```json block, e.g. [{"tool": "<name>", "arguments": {...}}]. '
    "Tool results come back in the next user message. Available tools:\n"
)


def describe_tools_as_text(tools: list[dict[str, Any]]) -> str:
    lines = []
    for t in tools:
        fn = t.get("function", t)
        desc = (fn.get("description") or "").strip().replace("\n", " ")
        params = json.dumps(fn.get("parameters", {}), ensure_ascii=False, separators=(",", ":"))
        lines.append(f"- {fn['name']}: {desc[:600]}\n  parameters: {params}")
    return "\n".join(lines)


def to_text_protocol(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite a native tool-calling history into plain system/user/assistant turns."""
    out: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for m in messages:
        role = m.get("role")
        if role == "system" and not out:
            out.append({"role": "system", "content": (m.get("content") or "") + TEXT_PROTOCOL_HEADER
                        + describe_tools_as_text(tools)})
        elif role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc["function"]
                names[tc["id"]] = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append({"tool": fn["name"], "arguments": args})
            text = (m.get("content") or "").strip()
            block = "```json\n" + json.dumps(calls, ensure_ascii=False) + "\n```"
            out.append({"role": "assistant", "content": f"{text}\n{block}".strip()})
        elif role == "tool":
            name = names.get(m.get("tool_call_id", ""), "tool")
            out.append({"role": "user", "content": f"[result of {name}]\n{m.get('content', '')}"})
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class OpenAICompatLLM:
    def __init__(
        self,
        *,
        base_url: str | None,
        model: str,
        api_key: str | None,
        timeout_s: float = 120.0,
        max_retries: int = 4,
        temperature: float = 0.1,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.temperature = temperature
        self.send_temperature = True
        self.native_tools = True
        # The SDK's own retries are disabled so backoff and logging stay in one place.
        self._client = AsyncOpenAI(base_url=base_url or None, api_key=api_key or "not-set",
                                   timeout=timeout_s, max_retries=0, http_client=http_client)

    @classmethod
    def from_env(cls, timeout_s: float = 120.0) -> OpenAICompatLLM:
        model = os.environ.get("LLM_MODEL", "").strip()
        if not model:
            raise LLMError("LLM_MODEL is not set (also set LLM_BASE_URL and LLM_API_KEY).")
        return cls(
            base_url=os.environ.get("LLM_BASE_URL") or None,
            model=model,
            api_key=os.environ.get("LLM_API_KEY") or None,
            timeout_s=timeout_s,
        )

    def _request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model}
        if self.native_tools:
            kwargs["messages"] = messages
            if tools:
                kwargs["tools"] = tools
        else:
            kwargs["messages"] = to_text_protocol(messages, tools)
        if self.send_temperature:
            kwargs["temperature"] = self.temperature
        return kwargs

    def _degrade_for(self, err: openai.BadRequestError) -> bool:
        """Drop an optional feature the provider rejected. True if there is something to retry."""
        msg = str(err).lower()
        if self.send_temperature and "temperature" in msg:
            log.warning("provider rejected temperature; retrying without it")
            self.send_temperature = False
            return True
        if self.native_tools and ("tool" in msg or "function" in msg):
            log.warning("provider rejected tool calling; switching to the JSON text protocol")
            self.native_tools = False
            return True
        return False

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        attempt = 0
        degradations = 0
        while True:
            try:
                resp = await asyncio.wait_for(
                    self._client.chat.completions.create(**self._request(messages, tools)),
                    timeout=self.timeout_s + 5,
                )
                return self._parse(resp, tools)
            except openai.BadRequestError as err:
                if degradations < 3 and self._degrade_for(err):
                    degradations += 1
                    continue
                raise LLMError(f"LLM request rejected: {err}") from err
            except (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError) as err:
                raise LLMError(f"LLM endpoint refused the request: {err}") from err
            except (*_RETRYABLE, asyncio.TimeoutError, openai.APIStatusError) as err:
                status = getattr(err, "status_code", None)
                if isinstance(err, openai.APIStatusError) and not isinstance(err, _RETRYABLE) and (
                    status is None or (status < 500 and status != 429)
                ):
                    raise LLMError(f"LLM request failed: {err}") from err
                attempt += 1
                if attempt > self.max_retries:
                    raise LLMError(f"LLM unavailable after {attempt} attempts: {err!r}") from err
                delay = min(30.0, 2 ** attempt) * (0.5 + random.random() / 2)
                log.warning("LLM call failed (%s); retry %d in %.1fs", type(err).__name__, attempt, delay)
                await asyncio.sleep(delay)

    def _parse(self, resp: Any, tools: list[dict[str, Any]]) -> LLMResponse:
        if not getattr(resp, "choices", None):
            raise LLMError("LLM returned no choices")
        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            raw = fn.arguments or "{}"
            try:
                args = json.loads(raw)
                err = None if isinstance(args, dict) else "arguments must be a JSON object"
            except json.JSONDecodeError:
                args, err = {}, "arguments were not valid JSON"
            extra = {k: v for k, v in (getattr(tc, "model_extra", None) or {}).items() if v is not None}
            calls.append(
                ToolCall(tc.id or new_call_id(), fn.name, args if isinstance(args, dict) else {}, err, extra=extra)
            )
        if not calls:
            known = {t.get("function", t).get("name") for t in tools}
            calls = parse_tool_calls_from_text(content, known)
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=content,
            tool_calls=calls,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            finish_reason=getattr(choice, "finish_reason", None),
        )
