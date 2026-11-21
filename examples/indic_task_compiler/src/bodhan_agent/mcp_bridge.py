"""MCP client bridge: StdioMCPBridge (default subprocess) and InProcessMCPBridge (for fake-transport tests).

CaMeL policy at the client boundary: third-party MCP clients receive bounded untrusted previews
from bodhan_mcp, whereas our agent strips `preview` here so untrusted text never reaches P-LLM.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Protocol

from bodhan_mcp.schemas import err_response
from bodhan_mcp.server import build_mcp_server
from bodhan_mcp.tools import BodhanToolkit
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from bodhan_agent.store import ArtifactStore


class MCPBridge(Protocol):
    async def __aenter__(self) -> MCPBridge: ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None: ...
    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class StdioMCPBridge:
    """Connects to `python -m bodhan_mcp` over stdio using the official MCP client SDK."""

    def __init__(self, *, work_dir: Path, cache_dir: Path | None = None) -> None:
        self.work_dir = work_dir.resolve()
        self.cache_dir = cache_dir
        self._stack = contextlib.AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> StdioMCPBridge:
        env = dict(os.environ)
        env["BODHAN_MCP_WORK_DIR"] = str(self.work_dir)
        if self.cache_dir is not None:
            env["BODHAN_CACHE_DIR"] = str(self.cache_dir)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp", "--work-dir", str(self.work_dir)],
            env=env,
        )
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self._stack.aclose()
        self._session = None

    async def list_tools(self) -> list[dict[str, Any]]:
        assert self._session is not None
        resp = await self._session.list_tools()
        tools: list[dict[str, Any]] = []
        for t in resp.tools:
            schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
            tools.append({"name": t.name, "description": t.description or "", "input_schema": schema})
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self._session is not None
        try:
            res = await self._session.call_tool(name, arguments)
            if not res.content:
                return err_response("empty_mcp_response", f"Tool '{name}' returned empty content.")
            raw_text = getattr(res.content[0], "text", None)
            if raw_text is None:
                return err_response("non_text_mcp_response", f"Tool '{name}' returned non-text content.")
            parsed = json.loads(raw_text)
            # CaMeL client-side boundary: strip any untrusted preview text immediately!
            return ArtifactStore.sanitize_tool_response_for_planner(parsed)
        except Exception as exc:
            return err_response("mcp_call_error", f"MCP call '{name}' failed: {exc}", retryable=True)


class InProcessMCPBridge:
    """Calls BodhanToolkit directly in-process (same schemas & CaMeL stripping as StdioMCPBridge)."""

    def __init__(self, toolkit: BodhanToolkit) -> None:
        self.toolkit = toolkit
        self._mcp = build_mcp_server(toolkit=toolkit)
        self._tool_schemas: list[dict[str, Any]] | None = None

    async def __aenter__(self) -> InProcessMCPBridge:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.toolkit.client.aclose()

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._tool_schemas is None:
            mcp_tools = await self._mcp.list_tools()
            self._tool_schemas = []
            for t in mcp_tools:
                schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}
                self._tool_schemas.append(
                    {"name": t.name, "description": t.description or "", "input_schema": schema}
                )
        return list(self._tool_schemas)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self.toolkit, name, None)
        if fn is None or not callable(fn):
            return err_response("unknown_tool", f"Unknown tool '{name}'.")
        try:
            raw = await fn(**arguments)
            return ArtifactStore.sanitize_tool_response_for_planner(raw)
        except TypeError as exc:
            return err_response("invalid_arguments", f"Invalid arguments for '{name}': {exc}")
        except Exception as exc:
            return err_response("tool_exception", f"Tool '{name}' raised {type(exc).__name__}: {exc}")
