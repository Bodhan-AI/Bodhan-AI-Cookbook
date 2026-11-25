"""Thin async wrapper around an MCP stdio session to the real `python -m bodhan_mcp` server."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class BodhanToolClient:
    def __init__(self, workdir: Path, cache_dir: Path | None = None, env_extra: dict[str, str] | None = None):
        self.workdir = workdir
        self.cache_dir = cache_dir
        self.env_extra = env_extra or {}
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: list[Any] = []

    async def __aenter__(self) -> BodhanToolClient:
        env = {**os.environ, "BODHAN_WORKDIR": str(self.workdir), "PYTHONIOENCODING": "utf-8", **self.env_extra}
        if self.cache_dir:
            env["BODHAN_CACHE_DIR"] = str(self.cache_dir)
        params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.tools = (await self.session.list_tools()).tools
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    def openai_tools(self) -> list[dict]:
        out = []
        for t in self.tools:
            out.append({"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema}})
        return out

    async def call(self, name: str, args: dict, timeout_s: float) -> dict:
        """Call a tool and return its structured result dict. Transport/validation problems become structured errors too."""
        assert self.session is not None
        try:
            res = await asyncio.wait_for(self.session.call_tool(name, args), timeout=timeout_s)
        except asyncio.TimeoutError:
            return _err("TOOL_TIMEOUT", f"{name} did not finish within {timeout_s:.0f}s (agent per-call timeout).", True,
                        "Retry once with a smaller input, or report the timeout.")
        except Exception as exc:  # transport failure: surface, never crash the loop
            return _err("MCP_TRANSPORT_ERROR", f"{type(exc).__name__}: {str(exc)[:200]}", False, "The tool server may have died; stop and report.")
        if res.structuredContent:
            data = res.structuredContent
            if set(data) == {"result"} and isinstance(data["result"], dict):
                data = data["result"]
            return data
        text = "".join(getattr(c, "text", "") for c in res.content)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
        return _err("TOOL_ERROR", text[:300] or "Tool returned no content.", False, "Check the arguments against the tool schema.")


def _err(code: str, message: str, retryable: bool, hint: str) -> dict:
    return {"ok": False, "error": {"error_code": code, "message": message, "retryable": retryable, "hint": hint}, "cost_inr": 0.0}
