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
from collections.abc import Callable
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


class ContextTooLong(LLMError):
    """The endpoint refused the request because the conversation is too long for the model."""


_CONTEXT_HINTS = ("context length", "context_length", "maximum context", "too many tokens", "request too large", "prompt is too long",
                  "reduce the length", "context window")


def llm_settings() -> tuple[str, str, str]:
    base, model, key = (os.environ.get(k, "").strip() for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"))
    missing = [k for k, v in (("LLM_BASE_URL", base), ("LLM_MODEL", model), ("LLM_API_KEY", key)) if not v]
    if missing:
        raise LLMConfigError(f"Missing LLM settings: {', '.join(missing)}. Set them in the environment or .env.")
    return base, model, key


RATE_LIMIT_ATTEMPTS = 6
MAX_RETRY_WAIT_S = 90.0
_WAIT_HINT = re.compile(r"(?:try again in|retry after|retry_after|resets_in_seconds)\W{0,3}([0-9]+(?:\.[0-9]+)?)\s*(ms|s|seconds?)?", re.I)


def retry_wait(exc: Exception, attempt: int) -> float:
    """Seconds to wait before retrying: the provider's Retry-After header or "try again in Xs" message when given,
    else exponential backoff; capped so one call never waits longer than MAX_RETRY_WAIT_S (the run deadline also applies)."""
    wait = float(2 ** attempt)
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    hinted = None
    try:
        if headers.get("retry-after") is not None:
            hinted = float(headers["retry-after"])
    except (TypeError, ValueError):
        hinted = None
    if hinted is None:
        m = _WAIT_HINT.search(str(exc))
        if m:
            hinted = float(m.group(1)) / (1000.0 if (m.group(2) or "").lower() == "ms" else 1.0)
    if hinted is not None:
        wait = max(wait, hinted + 0.5)
    return min(wait, MAX_RETRY_WAIT_S)


_DECODER = json.JSONDecoder()


def clean_tool_name(name: Any, known: set[str]) -> str:
    """Tool names as some open models emit them: "functions.finish", "finish<|channel|>commentary", " finish "."""
    text = str(name or "").split("<|")[0].strip().strip("\"'`")
    if text.startswith("functions."):
        text = text[len("functions."):]
    return text if text in known or not known else (next((k for k in known if text.endswith(k)), text))


def decode_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Arguments as a dict, also when a provider sends JSON inside a JSON string. Returns (args, error)."""
    value: Any = raw
    for _ in range(2):  # at most one extra level of quoting
        if isinstance(value, dict):
            return value, None
        if value is None or (isinstance(value, str) and not value.strip()):
            return {}, None
        if not isinstance(value, str):
            return {}, "arguments are not a JSON object"
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            return {}, f"arguments are not valid JSON: {exc}"
    return (value, None) if isinstance(value, dict) else ({}, "arguments are not a JSON object")


def _json_objects(text: str):
    """Every top-level JSON object in free text, left to right (no greedy first-brace-to-last-brace match)."""
    i = 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        yield obj
        i = end


_FUNCTION_TAG = re.compile(r"<function[=\s]+\"?([\w.\-]+)\"?\s*>?")


def _fallback_tool_call(content: str, known: set[str], schemas: dict[str, dict] | None = None) -> ToolCall | None:
    """Accept {"name": "...", "arguments": {...}} (or "tool"/"function") written as text, and the
    `<function=NAME>{...}</function>` form some open models produce."""
    for m in _FUNCTION_TAG.finditer(content or ""):
        name = clean_tool_name(m.group(1), known)
        obj = next(_json_objects(content[m.end():]), None)
        if name in known and isinstance(obj, dict):
            args, err = decode_arguments(obj.get("arguments", obj) if set(obj) <= {"name", "arguments"} else obj)
            if err is None:
                return ToolCall(f"fallback_{uuid.uuid4().hex[:8]}", name, args, json.dumps(args, ensure_ascii=False))
    for obj in _json_objects(content or ""):
        if not isinstance(obj, dict):
            continue
        fn = obj.get("function") if isinstance(obj.get("function"), dict) else {}
        name = clean_tool_name(obj.get("name") or obj.get("tool") or obj.get("tool_name") or fn.get("name"), known)
        args, err = decode_arguments(obj.get("arguments") or obj.get("args") or obj.get("parameters") or fn.get("arguments") or {})
        if name in known and err is None:
            return ToolCall(f"fallback_{uuid.uuid4().hex[:8]}", name, args, json.dumps(args, ensure_ascii=False))
        if not name and schemas:  # bare arguments, e.g. {"status": ..., "final_answer": ...}: the one tool they fit
            fits = [n for n, sch in schemas.items() if _fits(obj, sch)]
            if len(fits) == 1:
                return ToolCall(f"fallback_{uuid.uuid4().hex[:8]}", fits[0], obj, json.dumps(obj, ensure_ascii=False))
    return None


def _fits(obj: dict, schema: dict) -> bool:
    props, required = set((schema or {}).get("properties", {})), set((schema or {}).get("required", []))
    return bool(required) and required <= set(obj) <= props


TEXT_PROTOCOL = ("You can call exactly one tool per reply. Reply with ONLY a JSON object, no other text: "
                 '{"name": "<tool name>", "arguments": {...}}. Available tools (JSON schema of each):\n')


def to_text_protocol(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same conversation for an endpoint without native tool calling: tool calls and results become plain text."""
    spec = json.dumps([{"name": t["function"]["name"], "description": t["function"].get("description", ""),
                        "parameters": t["function"].get("parameters", {})} for t in tools], ensure_ascii=False)
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = [{"name": c["function"]["name"], "arguments": decode_arguments(c["function"].get("arguments"))[0]} for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": json.dumps(calls[0] if len(calls) == 1 else calls, ensure_ascii=False)})
        elif m.get("role") == "tool":
            out.append({"role": "user", "content": f"Tool result:\n{m.get('content', '')}"})
        else:
            out.append({k: v for k, v in m.items() if k in ("role", "content")})
    if out and out[0].get("role") == "system":  # one system message: some endpoints accept no second one
        out[0] = {"role": "system", "content": f"{out[0].get('content') or ''}\n\n{TEXT_PROTOCOL}{spec}"}
    else:
        out.insert(0, {"role": "user", "content": TEXT_PROTOCOL + spec})
    return out


def _error_body(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body.get("error", body) if isinstance(body.get("error", body), dict) else body
    return {}


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
        self._rejected_caps: set[str] = set()
        self.uncertain_attempts = 0
        self.no_tool_choice = False  # the endpoint rejected `tool_choice`
        self.text_protocol = False  # the endpoint rejected `tools`: tools are described in text instead
        self.notes: list[str] = []  # adaptations made for this endpoint (reported)
        self._echoed: set[str] = set()  # provider extra fields we send back
        self._blocked_extras: set[str] = set()  # extra fields the provider refused

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int | None = None,
                   can_retry: Callable[[], bool] | None = None) -> LLMReply:
        """`max_tokens` is sent only when the planner is priced, to bound its spend. Providers that
        reject it are tried with `max_completion_tokens`; if both are rejected, a priced call is refused
        (its cost could not be bounded) rather than sent without a cap. `can_retry` is asked before each
        retry, so a priced planner never retries beyond the budget."""
        last_exc: Exception | None = None
        self.uncertain_attempts = 0  # attempts that failed after the request may have reached the provider
        attempt, limit = 0, self.max_attempts
        while attempt < limit:
            attempt += 1
            if attempt > 1 and can_retry is not None and not can_retry():
                raise LLMError(f"Not retrying the planner call: the budget cannot cover another attempt "
                               f"(last error: {type(last_exc).__name__}: {str(last_exc)[:200]})")
            t0 = time.monotonic()
            known = {t["function"]["name"] for t in tools}
            try:
                resp = await self._create(messages, tools, max_tokens)
                return self._parse(resp, time.monotonic() - t0, known, {t["function"]["name"]: t["function"].get("parameters", {}) for t in tools})
            except asyncio.CancelledError:  # cut off (e.g. by the run deadline) while a request was in flight: it may be billed
                self.uncertain_attempts += 1
                raise
            except LLMError:
                raise
            except Exception as exc:  # network, 429, 5xx, malformed
                last_exc = exc
                status = getattr(exc, "status_code", None)
                if status is None or status >= 500:
                    self.uncertain_attempts += 1
                err, text = _error_body(exc), str(exc).lower()
                if status == 413 or (status in (400, 422) and any(h in text for h in _CONTEXT_HINTS)):
                    raise ContextTooLong(f"the planner's context is too long: {str(exc)[:200]}") from exc
                if status == 400 and (err.get("code") == "tool_use_failed" or "tool_use_failed" in text) and err.get("failed_generation"):
                    # Groq and others: the model's tool call did not match the schema; its text is often a usable call
                    fb = _fallback_tool_call(str(err["failed_generation"]), known)
                    if fb is not None:
                        self.uncertain_attempts += 1  # the failed generation was produced (and possibly billed)
                        return LLMReply("", [fb], 0, 0, time.monotonic() - t0, "tool_use_failed_recovered",
                                        {"role": "assistant", "content": None,
                                         "tool_calls": [{"id": fb.id, "type": "function", "function": {"name": fb.name, "arguments": fb.raw_arguments}}]},
                                        True)
                echoed = [k for k in self._echoed if k in text]
                if status in (400, 422) and echoed:  # the provider refuses a field we echoed from its own reply: stop echoing it
                    self._echoed -= set(echoed)
                    self._blocked_extras |= set(echoed)
                    for m in messages:
                        for k in echoed:
                            m.pop(k, None)
                            for c in m.get("tool_calls") or []:
                                c.pop(k, None)
                                if isinstance(c.get("function"), dict) and k not in ("name", "arguments"):
                                    c["function"].pop(k, None)
                    self.notes.append(f"the planner endpoint rejected echoed fields {echoed}; they are no longer sent back")
                    limit += 1
                    continue
                if status in (400, 422) and "tool_choice" in text and not self.no_tool_choice:
                    self.no_tool_choice = True
                    self.notes.append("the planner endpoint rejected tool_choice; it is no longer sent")
                    limit += 1  # this attempt taught us the endpoint's shape; it does not count
                    continue
                unsupported = any(w in text for w in ("not support", "unsupported", "not supported", "not available", "not enabled"))
                if (status in (400, 404, 422) and ("tools" in text or "function calling" in text or "tool use" in text) and unsupported
                        and (self.no_tool_choice or "tool_choice" not in text) and not self.text_protocol):
                    self.text_protocol = True
                    self.notes.append("the planner endpoint rejected native tool calling; tools are described in text and replies parsed as JSON")
                    limit += 1
                    continue
                if status in (400, 401, 403, 404, 422) and attempt == 1 and "tool" not in text:
                    break  # configuration error; retrying will not help
                if status == 429:  # rate limits pass: allow more attempts, waiting as long as the provider asks (capped)
                    limit = max(limit, RATE_LIMIT_ATTEMPTS)
                if attempt < limit:
                    await asyncio.sleep(retry_wait(exc, attempt))
        raise LLMError(f"LLM call failed after retries: {type(last_exc).__name__}: {str(last_exc)[:300]}")

    async def _create(self, messages, tools, max_tokens):
        if self.text_protocol:
            base = {"model": self.model, "messages": to_text_protocol(messages, tools)}
        else:
            base = {"model": self.model, "messages": messages, "tools": tools}
            if not self.no_tool_choice:
                base["tool_choice"] = "auto"
        if not max_tokens:
            return await self.client.chat.completions.create(**base)
        for cap in [f for f in ("max_tokens", "max_completion_tokens") if f not in self._rejected_caps]:
            try:
                return await self.client.chat.completions.create(**base, **{cap: max_tokens})
            except Exception as exc:
                if getattr(exc, "status_code", None) == 400 and cap in str(exc):
                    self._rejected_caps.add(cap)
                    continue
                raise
        raise LLMError("This planner endpoint rejects both max_tokens and max_completion_tokens, so the cost of a priced "
                       "planner call cannot be bounded. Run without --llm-*-inr-per-1m prices, or use an endpoint that accepts an output cap.")

    def _parse(self, resp: Any, latency: float, known: set[str], schemas: dict[str, dict] | None = None) -> LLMReply:
        choice = resp.choices[0]
        msg = choice.message
        usage = getattr(resp, "usage", None)
        calls: list[ToolCall] = []
        native: list[dict[str, Any]] = []  # the tool calls as the provider sent them, extras included
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments
            args, err = decode_arguments(raw)
            call_id = tc.id or f"call_{uuid.uuid4().hex[:8]}"
            calls.append(ToolCall(call_id, clean_tool_name(tc.function.name, known), args, raw if isinstance(raw, str) else json.dumps(args), err))
            fn_extra = {k: v for k, v in (getattr(tc.function, "model_extra", None) or {}).items() if k not in self._blocked_extras}
            call_extra = {k: v for k, v in (getattr(tc, "model_extra", None) or {}).items() if k not in self._blocked_extras}
            self._echoed |= set(fn_extra) | set(call_extra)
            native.append({**call_extra, "id": call_id, "type": "function",
                           "function": {**fn_extra, "name": calls[-1].name, "arguments": calls[-1].raw_arguments or "{}"}})
        content = msg.content or ""
        fallback = False
        if not calls and content:
            fb = _fallback_tool_call(content, known, schemas)
            if fb:
                calls, fallback = [fb], True
        # Echo the provider's own non-standard fields back (e.g. reasoning_content, Gemini thought signatures in
        # extra_content): some providers reject the next turn without them. Standard OpenAI-only fields (refusal,
        # annotations) are not echoed, because other providers reject them as unknown.
        assistant: dict[str, Any] = {k: v for k, v in (getattr(msg, "model_extra", None) or {}).items()
                                     if v is not None and k not in self._blocked_extras}
        self._echoed |= set(assistant)
        assistant.update({"role": "assistant", "content": content or None})
        if calls:
            assistant["tool_calls"] = native if native else [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]
            if fallback:
                assistant["content"] = None
        elif not content:
            # an empty turn carries nothing to continue from; echoing its reasoning alone is refused by some
            # gateways (seen live: 400 "Expected a(n) 'messages' array element to be an object")
            assistant = {"role": "assistant", "content": ""}
        return LLMReply(content, calls, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0,
                        latency, choice.finish_reason, assistant, fallback)
