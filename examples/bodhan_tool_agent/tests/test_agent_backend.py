"""McpToolBackend against a small in-process MCP server: schemas, results and error envelopes."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from bodhan_agent.tools_backend import McpToolBackend


class EchoResult(BaseModel):
    echoed: str
    cost_inr: float = 0.0
    warnings: list[str] = []


def build() -> MCPServer:
    server = MCPServer("echo")

    @server.tool()
    async def echo(text: Annotated[str, Field(description="Text to echo back.")], fail: bool = False) -> EchoResult:
        """Echo text back."""
        if fail:
            raise ToolError(json.dumps({"code": "invalid_argument", "message": "asked to fail",
                                        "hint": "pass fail=false", "retryable": False}))
        return EchoResult(echoed=text)

    return server


def run(coro_fn: Any) -> Any:
    async def go() -> Any:
        async with McpToolBackend(build()) as backend:
            return await coro_fn(backend)

    return asyncio.run(go())


def test_list_tools_gives_openai_schemas() -> None:
    specs = run(lambda b: b.list_tools())
    assert [s.name for s in specs] == ["echo"]
    schema = specs[0].openai_schema()
    params = schema["function"]["parameters"]
    assert schema["function"]["description"] == "Echo text back."
    assert params["required"] == ["text"] and "title" not in params
    assert params["properties"]["text"]["description"] == "Text to echo back."


def test_structured_result() -> None:
    out = run(lambda b: b.call("echo", {"text": "hi"}))
    assert out.ok and out.data["echoed"] == "hi"


def test_tool_error_envelope_is_parsed() -> None:
    out = run(lambda b: b.call("echo", {"text": "hi", "fail": True}))
    assert not out.ok and out.error_code == "invalid_argument" and out.error["hint"] == "pass fail=false"


def test_bad_arguments_and_unknown_tool_do_not_raise() -> None:
    bad = run(lambda b: b.call("echo", {"fail": "maybe"}))
    assert not bad.ok and bad.error_code == "tool_error"
    unknown = run(lambda b: b.call("nope", {}))
    assert not unknown.ok
