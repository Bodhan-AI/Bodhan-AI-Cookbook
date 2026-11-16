"""The agent's connection to the Bodhan MCP server.

By default the agent launches ``python -m bodhan_mcp`` as a subprocess and talks MCP over stdio,
exactly as any third-party client would. ``--mcp inproc`` (and the tests) connect to an in-process
server object instead, still through the MCP protocol layer.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from bodhan_mcp.errors import parse_error_text


@dataclass
class ToolOutcome:
    ok: bool
    data: dict[str, Any]
    error: dict[str, Any] | None = None
    raw_text: str = ""


class MCPLink:
    def __init__(self, server: Any | None = None, *, roots: list[Path], output_dir: Path, log_path: Path | None = None) -> None:
        self._server = server
        self.roots = roots
        self.output_dir = output_dir
        self.log_path = log_path
        self._stack: AsyncExitStack | None = None
        self.client: Client | None = None
        self.tools: list[Any] = []

    async def __aenter__(self) -> MCPLink:
        self._stack = AsyncExitStack()
        if self._server is None:
            env = {
                **os.environ,
                "BODHAN_MCP_ROOTS": os.pathsep.join(str(r) for r in self.roots),
                "BODHAN_MCP_OUTPUT_DIR": str(self.output_dir),
                "PYTHONIOENCODING": "utf-8",
            }
            target: Any = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)
        else:
            target = self._server
        self.client = await self._stack.enter_async_context(Client(target, read_timeout_seconds=600))
        self.tools = (await self.client.list_tools()).tools
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack:
            await self._stack.aclose()

    def openai_tools(self, extra: list[dict]) -> list[dict]:
        """MCP tool schemas as OpenAI function tools, each with an added 'reason' field for the trace."""
        out = []
        for t in self.tools:
            schema = json.loads(json.dumps(t.input_schema))
            schema.setdefault("properties", {})["reason"] = {
                "type": "string", "description": "One sentence: why this call is the right next step. Recorded in the trace.",
            }
            schema.pop("title", None)
            out.append({"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": schema}})
        return out + extra

    async def call(self, name: str, args: dict[str, Any], timeout_s: float) -> ToolOutcome:
        assert self.client is not None
        try:
            res = await asyncio.wait_for(self.client.call_tool(name, args, read_timeout_seconds=timeout_s), timeout=timeout_s + 5)
        except TimeoutError:
            return ToolOutcome(False, {}, {"code": "tool_timeout", "message": f"{name} did not finish within {timeout_s:.0f}s.",
                                           "hint": "Retry with smaller input, or report the timeout.", "retryable": True})
        except Exception as exc:  # noqa: BLE001 - protocol/transport failures become tool errors for the planner
            return ToolOutcome(False, {}, {"code": "mcp_error", "message": f"{type(exc).__name__}: {exc}", "retryable": True})
        text = res.content[0].text if res.content and hasattr(res.content[0], "text") else ""
        if res.is_error:
            err = parse_error_text(text) or {"code": "tool_error", "message": text[:1000]}
            return ToolOutcome(False, {}, err, text)
        data = res.structured_content
        if data is None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"text": text}
        return ToolOutcome(True, data, None, text)

    async def usage(self) -> dict[str, Any]:
        """Authoritative spend for this server process (includes calls inside failed tools)."""
        assert self.client is not None
        try:
            r = await self.client.read_resource("bodhan://usage")
            return json.loads(r.contents[0].text)
        except Exception:  # noqa: BLE001
            return {}
