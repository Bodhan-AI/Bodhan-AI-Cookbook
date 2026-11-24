"""Configuration and the agent's two connections: the LLM and the MCP tool server.

The LLM is reached through the `openai` SDK pointed at any OpenAI-compatible endpoint (LLM_BASE_URL /
LLM_MODEL / LLM_API_KEY). Only portable fields are sent: model, messages, tools, tool_choice="auto". No
temperature/top_p (some models reject non-defaults), no forced tool_choice (some models reject it), no
response_format or `strict` (not universally supported). Provider-specific settings come from
LLM_EXTRA_BODY. Tool arguments are parsed and validated by the caller.

The tools come from bodhan_mcp over MCP. By default the server is spawned as a subprocess over stdio
(`python -m bodhan_mcp`), exactly as any third-party client would use it; tests pass the MCPServer object
instead (in-process transport).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import openai
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError
from openai import AsyncOpenAI

from bodhan_mcp.client import clean_env_value, redact


class ConfigError(Exception):
    """Configuration is missing or invalid; the message says exactly what to set."""


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    model: str
    api_key: str
    extra_body: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMSettings:
        env = os.environ if env is None else env
        values = {name: clean_env_value(env.get(name)) for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigError(
                f"Missing LLM configuration: {', '.join(missing)}. Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY "
                "for any OpenAI-compatible endpoint (e.g. LLM_BASE_URL=https://api.openai.com/v1, LLM_MODEL=gpt-6-luna) "
                "in the environment or a .env file. See .env.example."
            )
        extra_raw = clean_env_value(env.get("LLM_EXTRA_BODY"))
        extra: dict = {}
        if extra_raw:
            try:
                extra = json.loads(extra_raw)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"LLM_EXTRA_BODY is not valid JSON ({exc}). Example: {{\"reasoning_effort\": \"none\"}}") from None
            if not isinstance(extra, dict):
                raise ConfigError("LLM_EXTRA_BODY must be a JSON object, e.g. {\"reasoning_effort\": \"none\"}.")
        return cls(values["LLM_BASE_URL"], values["LLM_MODEL"], values["LLM_API_KEY"], extra)


@dataclass
class RunConfig:
    task: str
    files: list[Path]
    out_dir: Path
    max_steps: int = 30
    max_cost_inr: float = 20.0
    call_timeout: float = 180.0
    max_seconds: float = 600.0

    @property
    def artifacts_dir(self) -> Path:
        return self.out_dir / "artifacts"

    @property
    def work_dir(self) -> Path:
        return self.out_dir / "work"


class LLMError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # "auth" | "config" | "bad_request" | "transient" | "bad_response"


@dataclass
class ToolCall:
    id: str
    name: str
    raw_arguments: str
    arguments: dict | None
    error: str | None = None


@dataclass
class LLMReply:
    text: str
    tool_calls: list[ToolCall]
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    message: dict = field(default_factory=dict)  # assistant message to append to the history


def parse_arguments(raw) -> tuple[dict | None, str | None]:
    if isinstance(raw, dict):
        return raw, None
    text = (raw or "").strip()
    if not text:
        return {}, None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"arguments are not valid JSON ({exc.msg})"
    if not isinstance(value, dict):
        return None, "arguments must be a JSON object"
    return value, None


class LLMClient:
    def __init__(self, settings: LLMSettings, timeout: float = 120.0) -> None:
        self.settings = settings
        self._client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=timeout, max_retries=2)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        kwargs: dict = {"model": self.settings.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.settings.extra_body:
            kwargs["extra_body"] = self.settings.extra_body
        started = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            raise LLMError("auth", f"LLM authentication failed ({exc.status_code}): {redact(str(exc))[:300]}. "
                                   "Check LLM_API_KEY.") from None
        except openai.NotFoundError as exc:
            raise LLMError("config", f"LLM endpoint or model not found: {redact(str(exc))[:300]}. "
                                     "Check LLM_BASE_URL and LLM_MODEL.") from None
        except openai.BadRequestError as exc:
            raise LLMError("bad_request", f"LLM rejected the request: {redact(str(exc))[:400]}") from None
        except (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError,
                openai.InternalServerError) as exc:
            raise LLMError("transient", f"LLM unavailable after retries: {redact(str(exc))[:300]}") from None
        except openai.APIError as exc:
            raise LLMError("transient", f"LLM error: {redact(str(exc))[:300]}") from None
        latency = int((time.perf_counter() - started) * 1000)
        if not getattr(resp, "choices", None):
            raise LLMError("bad_response", "LLM returned no choices.")
        choice = resp.choices[0]
        msg = choice.message
        calls: list[ToolCall] = []
        raw_calls = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            call_id = tc.id or f"call_{uuid.uuid4().hex[:12]}"
            args, err = parse_arguments(fn.arguments)
            raw_args = fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments)
            calls.append(ToolCall(call_id, fn.name or "", raw_args or "{}", args, err))
            raw_calls.append({"id": call_id, "type": "function",
                              "function": {"name": fn.name or "", "arguments": raw_args or "{}"}})
        text = msg.content or ""
        message: dict = {"role": "assistant", "content": text or None}
        if raw_calls:
            message["tool_calls"] = raw_calls
        usage = getattr(resp, "usage", None)
        return LLMReply(text=text, tool_calls=calls, finish_reason=choice.finish_reason,
                        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                        latency_ms=latency, message=message)


def mcp_tool_to_openai(tool) -> dict:
    """Convert an MCP tool definition into an OpenAI-style function tool."""
    schema = dict(tool.input_schema or {"type": "object", "properties": {}})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return {"type": "function",
            "function": {"name": tool.name, "description": (tool.description or "")[:1500], "parameters": schema}}


@dataclass
class ToolOutcome:
    ok: bool
    data: dict = field(default_factory=dict)
    error: dict | None = None
    latency_ms: int = 0

    @property
    def cost_inr(self) -> float:
        return float(self.data.get("cost_inr") or 0.0) if self.ok else 0.0


def _parse_error(text: str) -> dict:
    start = text.find("{")
    if start >= 0:
        try:
            body = json.loads(text[start:])
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                return body["error"]
        except json.JSONDecodeError:
            pass
    return {"code": "TOOL_ERROR", "message": text[:600], "hint": ""}


def stdio_server(work_dir: Path, cache_dir: Path | None = None) -> StdioServerParameters:
    # The child does not inherit the environment by default; pass it explicitly, minus the LLM key
    # (the server never needs it).
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
    env["BODHAN_MCP_WORKDIR"] = str(work_dir)
    if cache_dir is not None:
        env["BODHAN_CACHE_DIR"] = str(cache_dir)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)


class ToolBox:
    def __init__(self, server) -> None:
        self._server = server
        self._client: Client | None = None
        self.tools: list = []

    async def __aenter__(self) -> ToolBox:
        self._client = Client(self._server)
        await self._client.__aenter__()
        self.tools = list((await self._client.list_tools()).tools)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.__aexit__(*exc)

    @property
    def names(self) -> set[str]:
        return {t.name for t in self.tools}

    async def call(self, name: str, arguments: dict, timeout: float = 180.0) -> ToolOutcome:
        assert self._client is not None
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self._client.call_tool(name, arguments, read_timeout_seconds=timeout), timeout + 10)
        except TimeoutError:
            return ToolOutcome(False, error={"code": "TIMEOUT", "message": f"{name} did not finish within {timeout:.0f}s.",
                                             "hint": "Try a smaller input, or continue without this step."},
                               latency_ms=int((time.perf_counter() - started) * 1000))
        except MCPError as exc:
            return ToolOutcome(False, error={"code": "MCP_ERROR", "message": str(exc)[:500], "hint": ""},
                               latency_ms=int((time.perf_counter() - started) * 1000))
        latency = int((time.perf_counter() - started) * 1000)
        text = "".join(getattr(c, "text", "") for c in (result.content or []))
        if result.is_error:
            return ToolOutcome(False, error=_parse_error(text), latency_ms=latency)
        data = result.structured_content
        if data is None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"preview": text[:1000]}
        if isinstance(data, dict) and "result" in data and len(data) == 1 and isinstance(data["result"], dict):
            data = data["result"]
        return ToolOutcome(True, data=data, latency_ms=latency)
