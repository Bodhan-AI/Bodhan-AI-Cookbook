"""The agent's connection to the tool server: a real MCP client over stdio.

The agent uses exactly the interface a third-party client would, so whatever works for the
agent works for anyone else's client. The server runs as a subprocess (`python -m bodhan_mcp`).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@dataclass
class ToolOutcome:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)  # structured result when ok
    error: dict[str, Any] = field(default_factory=dict)  # {"code", "message", "hint", ...} when not ok
    latency_ms: int = 0

    @property
    def cost_inr(self) -> float:
        if self.ok:
            return float((self.data.get("usage") or {}).get("cost_inr") or 0.0)
        return float((self.error.get("details") or {}).get("spent_inr") or 0.0)


def _parse_error(text: str) -> dict[str, Any]:
    start = text.find("{")
    if start >= 0:
        try:
            obj = json.loads(text[start:])
            if isinstance(obj, dict) and isinstance(obj.get("error"), dict):
                return obj["error"]
        except json.JSONDecodeError:
            pass
    return {"code": "tool_error", "message": text.strip()[:500]}


class ToolBridge:
    def __init__(
        self, server_cmd: list[str] | None = None, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> None:
        self.server_cmd = server_cmd or [sys.executable, "-m", "bodhan_mcp"]
        self.env = env or {}
        self.cwd = cwd
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: list[dict[str, Any]] = []

    async def __aenter__(self) -> ToolBridge:
        params = StdioServerParameters(
            command=self.server_cmd[0], args=self.server_cmd[1:], env={**os.environ, **self.env}, cwd=self.cwd
        )
        read, write = (await self._stack.enter_async_context(stdio_client(params)))[:2]
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema} for t in listed.tools
        ]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict[str, Any], timeout_s: float) -> ToolOutcome:
        assert self.session is not None
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(
                self.session.call_tool(name, arguments, read_timeout_seconds=timeout_s), timeout_s + 5
            )
        except TimeoutError:
            return ToolOutcome(
                False,
                error={"code": "timeout", "message": f"{name} did not finish within {timeout_s:.0f}s."},
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # transport/protocol failure: report, don't crash the run
            return ToolOutcome(
                False,
                error={"code": "tool_transport_error", "message": f"{type(exc).__name__}: {exc}"},
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        latency = int((time.perf_counter() - t0) * 1000)
        text = "\n".join(getattr(c, "text", "") for c in (res.content or []))
        if res.is_error:
            return ToolOutcome(False, error=_parse_error(text), latency_ms=latency)
        data = res.structured_content
        if data is None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"text": text}
        if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
            data = data["result"]  # tools returning plain dicts are wrapped by the SDK
        return ToolOutcome(True, data=data if isinstance(data, dict) else {"result": data}, latency_ms=latency)
