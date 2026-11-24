"""How the agent reaches the tools: in-process (same code as the server) or over MCP stdio."""
from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any


def parse_tool_error(text: str) -> dict[str, Any]:
    i = text.find("{")
    if i >= 0:
        try:
            return json.loads(text[i:])
        except ValueError:
            pass
    return {"ok": False, "code": "tool_error", "message": text[:400]}


def _simplify(schema: Any) -> Any:
    """Collapse anyOf[X, null] to X: some OpenAI-compatible providers choke on nullable unions."""
    if isinstance(schema, dict):
        if "anyOf" in schema:
            opts = [o for o in schema["anyOf"] if o.get("type") != "null"]
            if len(opts) == 1:
                rest = {k: v for k, v in schema.items() if k not in ("anyOf", "default")}
                return _simplify({**opts[0], **rest})
        return {k: _simplify(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_simplify(x) for x in schema]
    return schema


def to_openai(name: str, description: str, input_schema: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": _simplify(input_schema)}}


class InProcessBackend:
    name = "in-process"

    async def start(self, env: dict[str, str]) -> None:
        os.environ.update(env)
        from bodhan_mcp.server import mcp
        self._mcp = mcp

    async def list_tools(self) -> list[dict]:
        return [to_openai(t.name, t.description or "", t.inputSchema) for t in await self._mcp.list_tools()]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        from mcp.server.fastmcp.exceptions import ToolError
        try:
            r = await self._mcp.call_tool(name, args)
        except ToolError as e:
            return {"ok": False, **parse_tool_error(str(e))}
        except Exception as e:  # noqa: BLE001 - e.g. argument validation errors
            return {"ok": False, "code": "invalid_arguments", "message": str(e)[:500],
                    "hint": "Check the argument names and types against the tool schema."}
        if isinstance(r, tuple):
            return {"ok": True, **r[1]}
        if isinstance(r, dict):
            return {"ok": True, **r}
        return {"ok": True, "text": getattr(r[0], "text", str(r[0]))}

    async def close(self) -> None:
        pass


class McpStdioBackend:
    name = "mcp-stdio"

    async def start(self, env: dict[str, str]) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self._stack = contextlib.AsyncExitStack()
        params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"],
                                       env={**os.environ, **env})
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def list_tools(self) -> list[dict]:
        r = await self._session.list_tools()
        return [to_openai(t.name, t.description or "", t.inputSchema) for t in r.tools]

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        r = await self._session.call_tool(name, args)
        text = "".join(getattr(c, "text", "") for c in r.content)
        if r.isError:
            return {"ok": False, **parse_tool_error(text)}
        if getattr(r, "structuredContent", None):
            return {"ok": True, **r.structuredContent}
        return {"ok": True, "text": text}

    async def close(self) -> None:
        await self._stack.aclose()
