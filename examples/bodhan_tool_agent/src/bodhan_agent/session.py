"""MCP stdio session to `python -m bodhan_mcp`: the agent uses the server exactly as any MCP client would."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HIDDEN_PARAMS = {"out_dir"}  # infrastructure arguments the agent sets itself; the LLM never sees them
DESC_LIMIT = 200
PARAM_DESC_LIMIT = 80


def _payload(result) -> dict:
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = getattr(result, "isError", False)
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    text = "".join(getattr(c, "text", "") for c in (result.content or []))
    if is_error:
        return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": text[:500] or "tool call rejected",
                                       "hint": "Check the argument names and types against the tool schema.",
                                       "retryable": False}}
    if sc:
        return sc.get("result", sc) if isinstance(sc, dict) else {"ok": True, "result": sc}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": True, "text": text}


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class ToolSession:
    def __init__(self, work_dir: Path, *, max_cost_inr: float | None, call_timeout_s: float = 300.0):
        self.work_dir = work_dir
        self.max_cost_inr = max_cost_inr
        self.call_timeout_s = call_timeout_s
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.tools: dict[str, object] = {}

    async def __aenter__(self) -> ToolSession:
        env = {**os.environ, "BODHAN_WORKSPACE": str(self.work_dir)}
        if self.max_cost_inr is not None:
            env["BODHAN_MAX_COST_INR"] = str(self.max_cost_inr)  # hard cap enforced by the server too
        params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        self.tools = {t.name: t for t in (await self.session.list_tools()).tools}
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict, timeout_s: float | None = None) -> dict:
        if name not in self.tools:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": f"Unknown tool {name!r}.",
                                           "hint": f"Available tools: {sorted(self.tools)}", "retryable": False}}
        args = {k: v for k, v in arguments.items() if v is not None}
        if "out_dir" in self._params(name):
            args["out_dir"] = str(self.work_dir)
        try:
            result = await asyncio.wait_for(self.session.call_tool(name, args), timeout_s or self.call_timeout_s)
        except asyncio.TimeoutError:
            return {"ok": False, "error": {"code": "UPSTREAM_TIMEOUT",
                                           "message": f"{name} did not finish within {timeout_s or self.call_timeout_s:.0f}s.",
                                           "hint": "Try a smaller input (fewer pages, shorter audio).", "retryable": True}}
        return _payload(result)

    def _params(self, name: str) -> dict:
        tool = self.tools[name]
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {}) or {}
        return schema.get("properties", {})

    def llm_specs(self, names: list[str] | None = None) -> list[dict]:
        """Compact OpenAI tool specs: first paragraph of each description, short param descriptions, no out_dir."""
        specs = []
        for name, tool in self.tools.items():
            if names and name not in names:
                continue
            schema = json.loads(json.dumps(getattr(tool, "input_schema", None) or tool.inputSchema))
            props = {k: v for k, v in schema.get("properties", {}).items() if k not in HIDDEN_PARAMS}
            for key, v in list(props.items()):
                # Optional params arrive as anyOf [X, null]; send just X (they are optional via `required`).
                options = [o for o in v.pop("anyOf", []) if o.get("type") != "null"]
                if len(options) == 1:
                    v.update(options[0])
                elif options:
                    v["anyOf"] = options
                if v.get("default") is None:
                    v.pop("default", None)
                if "description" in v:
                    v["description"] = _short(v["description"], PARAM_DESC_LIMIT)
                v.pop("title", None)
                props[key] = v
            schema["properties"] = props
            schema.pop("title", None)
            if "required" in schema:
                schema["required"] = [r for r in schema["required"] if r in props]
            first_para = (tool.description or "").strip().split("\n\n")[0]
            specs.append({"type": "function", "function": {"name": name, "description": _short(first_para, DESC_LIMIT),
                                                           "parameters": schema}})
        return specs
