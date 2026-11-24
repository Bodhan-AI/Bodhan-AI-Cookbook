"""Talks to the Bodhan MCP server: in-process (default) or as a stdio subprocess.

Both paths go through the MCP client, so the agent sees exactly the tool
schemas and error envelopes any third-party client sees.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mcp import Client, MCPError
from mcp.client.stdio import StdioServerParameters

from bodhan_mcp.errors import parse_tool_error


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": clean_schema(self.input_schema),
            },
        }


@dataclass
class ToolOutcome:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    @property
    def error_code(self) -> str | None:
        return (self.error or {}).get("code")

    @classmethod
    def failure(cls, code: str, message: str, hint: str = "", retryable: bool = False) -> ToolOutcome:
        return cls(False, {}, {"code": code, "message": message, "hint": hint, "retryable": retryable})


class ToolBackend(Protocol):
    async def list_tools(self) -> list[ToolSpec]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome: ...


def clean_schema(schema: Any) -> Any:
    """Drop pydantic `title` noise; some providers choke on it next to `$ref`/`anyOf`."""
    if isinstance(schema, dict):
        return {k: clean_schema(v) for k, v in schema.items() if k != "title" or not isinstance(v, str)}
    if isinstance(schema, list):
        return [clean_schema(v) for v in schema]
    return schema


def _text_of(result: Any) -> str:
    parts = [getattr(c, "text", "") for c in getattr(result, "content", None) or []]
    return "\n".join(p for p in parts if p)


def outcome_from_result(result: Any) -> ToolOutcome:
    text = _text_of(result)
    if getattr(result, "is_error", False):
        err = parse_tool_error(text)
        if err is None:
            err = {"code": "tool_error", "message": text or "tool failed without a message", "hint": "",
                   "retryable": False}
        return ToolOutcome(False, {}, err)
    data = getattr(result, "structured_content", None)
    if data is None and text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"text": text}
    if not isinstance(data, dict):
        data = {"result": data}
    return ToolOutcome(True, data)


class McpToolBackend:
    """Async context manager wrapping an MCP `Client`."""

    def __init__(self, target: Any) -> None:
        self._target = target
        self._stack: AsyncExitStack | None = None
        self._client: Client | None = None

    @classmethod
    def inprocess(cls) -> McpToolBackend:
        from bodhan_mcp.server import build_server

        return cls(build_server())

    @classmethod
    def stdio(cls, env: dict[str, str] | None = None) -> McpToolBackend:
        child_env = dict(os.environ if env is None else env)
        # Make the package importable in the child even when it is not pip-installed.
        src_dir = str(Path(__file__).resolve().parents[1])
        existing = child_env.get("PYTHONPATH", "")
        if src_dir not in existing.split(os.pathsep):
            child_env["PYTHONPATH"] = os.pathsep.join(p for p in (src_dir, existing) if p)
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        return cls(StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=child_env))

    async def __aenter__(self) -> McpToolBackend:
        self._stack = AsyncExitStack()
        self._client = await self._stack.enter_async_context(Client(self._target))
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._client = None

    def _require(self) -> Client:
        if self._client is None:
            raise RuntimeError("McpToolBackend used outside `async with`")
        return self._client

    async def list_tools(self) -> list[ToolSpec]:
        client = self._require()
        specs: list[ToolSpec] = []
        cursor: str | None = None
        for _ in range(50):
            page = await client.list_tools(cursor=cursor) if cursor else await client.list_tools()
            specs.extend(ToolSpec(t.name, t.description or "", dict(t.input_schema)) for t in page.tools)
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return specs

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        client = self._require()
        try:
            result = await client.call_tool(name, arguments)
        except MCPError as err:
            return ToolOutcome.failure("transport_error", f"MCP call failed: {err}", retryable=True)
        return outcome_from_result(result)
