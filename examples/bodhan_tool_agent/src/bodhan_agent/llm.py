"""OpenAI-compatible LLM client for the agent's brain (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY).

Only standard chat-completions features are used. Each one has a fallback:
* tool calling → if the provider rejects `tools`, or the model writes its call as JSON text instead
  of a structured tool call (common with small local models), the call is parsed from the text;
* JSON mode → if `response_format` is rejected, JSON is extracted from plain text;
* rate limits → 429s wait for the time the provider asks for ("try again in 7.5s"), then retry.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import openai
from openai import AsyncOpenAI

from bodhan_core.config import Settings

# Includes hidden reasoning tokens. Groq's free tier counts this cap against its 8k tokens/minute, so keep it modest.
DEFAULT_MAX_TOKENS = 2048
MIN_MAX_TOKENS = 512
_TOO_LARGE_RE = re.compile(r"Limit (\d+), Requested (\d+)")

# Provider 400s that mean "the model's own output was malformed", not "your request is wrong". Retrying helps.
# Groq (all seen in live eval runs): tool_use_failed, "Parsing failed ... failed_generation", and gpt-oss emitting a
# tool call on the wrong channel ("Tool choice is none, but model called a tool").
MALFORMED_GENERATION = ("tool_use_failed", "failed to call a function", "failed_generation", "parsing failed",
                        "but model called a tool")

class LLMError(Exception):
    """The LLM could not be reached or kept failing; the run should stop and report it."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    parse_error: str | None = None


@dataclass
class LLMReply:
    content: str
    tool_calls: list[ToolCall]
    usage: dict = field(default_factory=dict)
    latency_ms: int = 0
    waited_s: float = 0.0
    fallback: str | None = None  # "text-tool-call" / "text-json" when a fallback parser was used

    def assistant_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                                 for c in self.tool_calls]
        return msg


_WAIT_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)


def retry_after_seconds(err: Exception) -> float | None:
    resp = getattr(err, "response", None)
    if resp is not None:
        value = resp.headers.get("retry-after")
        try:
            if value:
                return float(value)
        except ValueError:
            pass
    m = _WAIT_RE.search(str(err))
    if m:
        return int(m.group(1) or 0) * 60 + float(m.group(2))
    return None


def extract_json(text: str) -> dict | None:
    """First JSON object in text (handles ```json fences and <tool_call> tags)."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?|</?tool_call>", "", text)
    start = cleaned.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                esc = ch == "\\" and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(cleaned[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
                    break
        start = cleaned.find("{", start + 1)
    return None


def _text_tool_call(text: str, tool_names: set[str]) -> ToolCall | None:
    obj = extract_json(text)
    if not obj:
        return None
    name = obj.get("tool") or obj.get("name") or (obj.get("function") or {}).get("name")
    args = obj.get("arguments", obj.get("parameters", obj.get("args", {})))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if name in tool_names and isinstance(args, dict):
        return ToolCall(id=f"text_{int(time.time() * 1000)}", name=name, arguments=args)
    if name is None and "finish" in tool_names and isinstance(obj.get("answer"), str):
        # Seen live: the model wrote finish's arguments as a bare JSON reply instead of a tool call.
        args = {k: obj[k] for k in ("answer", "artifacts", "assumptions") if k in obj}
        return ToolCall(id=f"text_{int(time.time() * 1000)}", name="finish", arguments=args)
    return None


def tools_as_text(tools: list[dict]) -> str:
    lines = ["You can call these tools. To call one, reply with ONLY a JSON object: "
             '{"tool": "<name>", "arguments": {...}}.']
    for t in tools:
        f = t["function"]
        params = ", ".join(f"{k}{'' if k in f['parameters'].get('required', []) else '?'}"
                           for k in f["parameters"].get("properties", {}))
        lines.append(f"- {f['name']}({params}): {f['description']}")
    return "\n".join(lines)


class LLM:
    def __init__(
        self,
        settings: Settings,
        *,
        timeout_s: float = 120.0,
        max_attempts: int = 6,
        max_wait_s: float = 90.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if not (settings.llm_base_url and settings.llm_model):
            raise LLMError("LLM_BASE_URL and LLM_MODEL must be set (any OpenAI-compatible endpoint with tool calling).")
        self.model = settings.llm_model
        self.client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "none",
                                  timeout=timeout_s, max_retries=0)
        self.max_attempts, self.max_wait_s, self._sleep = max_attempts, max_wait_s, sleep
        self.timeout_s = timeout_s
        self.native_tools = True
        self.native_json = True
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "waited_s": 0.0}

    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None, json_mode: bool = False,
                   max_tokens: int | None = None, temperature: float = 0.0) -> LLMReply:
        started, waited = time.monotonic(), 0.0
        attempt, malformed, limited = 0, 0, 0  # limited: rate-limit waits, counted apart from failed attempts
        text_tools = not self.native_tools  # describe tools in text instead of native tool calling
        while True:
            attempt += 1
            kwargs: dict = {"model": self.model, "messages": messages, "temperature": temperature}
            # Always cap output: reasoning models at temperature 0 can loop and waste the token budget.
            kwargs["max_tokens"] = max_tokens = max_tokens or DEFAULT_MAX_TOKENS
            if tools and not text_tools:
                kwargs["tools"] = tools
            elif tools:
                kwargs["messages"] = [{"role": "system", "content": tools_as_text(tools)}, *_flatten(messages)]
            if json_mode and self.native_json:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                # Hard deadline: the SDK timeout is per read, so a response that trickles bytes never trips it.
                resp = await asyncio.wait_for(self.client.chat.completions.create(**kwargs), self.timeout_s)
                break
            except openai.RateLimitError as e:
                asked = retry_after_seconds(e)
                if asked and asked > self.max_wait_s:
                    # A daily quota, not a per-minute limit (seen live: Groq tokens-per-day, "try again in 11m53s").
                    # Waiting in short steps would only burn the run's time budget, so stop with a clear reason.
                    raise LLMError(f"LLM quota exhausted: the provider asks to wait {asked / 60:.0f} min "
                                   f"(more than {self.max_wait_s:.0f}s). Try later or use another LLM_MODEL. {e}") from None
                limited += 1
                wait = min(asked or 2.0 ** limited, self.max_wait_s)
                if limited >= self.max_attempts:
                    raise LLMError(f"LLM rate limit persisted after {limited} waits: {e}") from None
                waited += wait
                await self._sleep(wait + 0.25)
            except openai.BadRequestError as e:
                msg = str(e).lower()
                if any(m in msg for m in MALFORMED_GENERATION):
                    # The model produced a malformed tool call or unparsable output; ask again.
                    # At temperature 0 it repeats the same bad output (seen live), so sample on the retries.
                    malformed += 1
                    temperature = max(temperature, 0.7)
                    if malformed >= 3:
                        if not (tools and not text_tools):
                            raise LLMError(f"LLM kept producing malformed tool calls: {e}") from None
                        # Native tool calls keep failing (seen live: gpt-oss-20b on Groq, "Tool choice is none, but
                        # model called a tool"). Ask for this one reply with the tools described in text instead.
                        text_tools, malformed = True, 1
                    continue
                if tools and not text_tools and ("tool" in msg or "function" in msg):
                    self.native_tools, text_tools = False, True  # provider has no tool calling: describe tools in text
                    continue
                if json_mode and self.native_json and ("response_format" in msg or "json" in msg):
                    self.native_json = False
                    continue
                raise LLMError(f"LLM rejected the request: {e}") from None
            except (TimeoutError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as e:
                if attempt >= self.max_attempts:
                    raise LLMError(f"LLM unavailable after {attempt} attempts: {type(e).__name__}") from None
                wait = min(2.0 ** attempt, 30.0)
                waited += wait
                await self._sleep(wait)
            except openai.APIStatusError as e:
                m = _TOO_LARGE_RE.search(str(e)) if e.status_code == 413 else None
                if m and attempt < self.max_attempts:
                    # Prompt + output cap exceeds the provider's per-request token limit: shrink the cap to fit.
                    smaller = max_tokens - (int(m.group(2)) - int(m.group(1))) - 64
                    if smaller >= MIN_MAX_TOKENS:
                        max_tokens = smaller
                        continue
                if e.status_code in (401, 403):
                    raise LLMError("LLM authentication failed: check LLM_API_KEY.") from None
                raise LLMError(f"LLM error {e.status_code}: {e}") from None

        choice = resp.choices[0]
        msg = choice.message
        content = msg.content or ""
        calls, fallback = [], None
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
                calls.append(ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else {}))
            except json.JSONDecodeError as e:
                calls.append(ToolCall(tc.id, tc.function.name, {}, parse_error=f"arguments were not valid JSON: {e}"))
        if tools and not calls:
            parsed = _text_tool_call(content, {t["function"]["name"] for t in tools})
            if parsed:
                calls = [parsed]
                fallback = "text-tools-after-malformed" if text_tools and self.native_tools else "text-tool-call"
        usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0,
                 "completion_tokens": getattr(resp.usage, "completion_tokens", 0) or 0} if resp.usage else {}
        self.usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self.usage["completion_tokens"] += usage.get("completion_tokens", 0)
        self.usage["calls"] += 1
        self.usage["waited_s"] = round(self.usage["waited_s"] + waited, 2)
        return LLMReply(content, calls, usage, int((time.monotonic() - started) * 1000), round(waited, 2), fallback)

    async def json(self, messages: list[dict], *, max_tokens: int | None = None) -> tuple[dict | None, LLMReply]:
        reply = await self.chat(messages, json_mode=True, max_tokens=max_tokens)
        return extract_json(reply.content), reply


def _flatten(messages: list[dict]) -> list[dict]:
    """For providers without tool calling: turn tool-call history into plain user/assistant text."""
    out = []
    for m in messages:
        if m["role"] == "tool":
            out.append({"role": "user", "content": f"Result of {m.get('name', 'tool')}: {m['content']}"})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            calls = [{"tool": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
                     for c in m["tool_calls"]]
            out.append({"role": "assistant", "content": json.dumps(calls[0] if len(calls) == 1 else calls)})
        else:
            out.append({k: v for k, v in m.items() if k in ("role", "content")})
    return out
