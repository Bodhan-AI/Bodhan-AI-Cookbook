from __future__ import annotations

import asyncio
import json
import os
import sys
import typing
from pathlib import Path
from time import monotonic

import pytest

from bodhan_agent.clients.mcp_session import McpSession, McpUnavailable, _server_error
from bodhan_agent.core.config import RunSettings
from bodhan_agent.schemas.mcp import AGENT_ERROR_CODES, McpError, McpResult
from bodhan_mcp.exceptions import ErrorCode

FAKE_SERVER = r"""
import json, os, sys
import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

TOOLS = ["echo", "fail", "fail_plain", "hang", "text_json", "text_only", "crash", "slow"]


def text(value):
    return [types.TextContent(type="text", text=value)]


async def list_tools(_ctx, _params):
    return types.ListToolsResult(
        tools=[types.Tool(name=name, description=f"{name} tool", input_schema={"type": "object"}) for name in TOOLS]
    )


async def call_tool(_ctx, params):
    args = params.arguments or {}
    name = params.name
    if name == "echo":
        data = {"echo": args, "usage": {"estimated_cost_inr": 0.2, "billing_uncertain": False}}
        return types.CallToolResult(content=text(json.dumps(data)), structured_content=data)
    if name == "fail":
        error = {"error": {"status": 502, "code": "upstream_error", "message": "chunk 3 failed", "hint": "retry later",
                           "retryable": True, "details": {"usage": {"estimated_cost_inr": 0.08,
                                                                    "billing_uncertain": False}}}}
        return types.CallToolResult(content=text(json.dumps(error)), is_error=True)
    if name == "fail_plain":
        return types.CallToolResult(content=text("something broke"), is_error=True)
    if name == "hang":
        await anyio.sleep(60)
    if name == "slow":
        await anyio.sleep(float(args.get("seconds", 0.2)))
        data = {"slept": args.get("seconds"), "tag": args.get("tag")}
        return types.CallToolResult(content=text(json.dumps(data)), structured_content=data)
    if name == "text_json":
        return types.CallToolResult(content=text(json.dumps({"from": "text"})))
    if name == "text_only":
        return types.CallToolResult(content=text("not json"))
    if name == "crash":
        os._exit(1)
    raise MCPError(types.INVALID_PARAMS, f"Unknown tool {name}")


async def main():
    print("fake server started", file=sys.stderr, flush=True)
    server = Server("fake", instructions="fake instructions", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


anyio.run(main)
"""


@pytest.fixture
def run_settings(tmp_path: Path) -> RunSettings:
    (tmp_path / "in").mkdir()
    return RunSettings.create("t", [str(tmp_path / "in")], str(tmp_path / "out"))


@pytest.fixture
def fake_command(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE_SERVER)
    return [sys.executable, str(script)]


def no_keys_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith(("BODHAN_", "LLM_"))}


def test_agent_codes_never_overlap_server_codes() -> None:
    assert AGENT_ERROR_CODES.isdisjoint(typing.get_args(ErrorCode))


def test_result_schema_rules() -> None:
    error = McpError(code="upstream_error", message="x", details={"usage": {"estimated_cost_inr": 0.1}})
    assert McpResult(tool="t", ok=False, error=error, latency_ms=0).usage == {"estimated_cost_inr": 0.1}
    assert McpResult(tool="t", ok=True, data={"usage": {"estimated_cost_inr": 0.2}}, latency_ms=0).usage
    for fields in ({"ok": True}, {"ok": False}, {"ok": False, "error": error, "data": {}}, {"ok": True, "tool": ""}):
        with pytest.raises(ValueError):
            McpResult(**{"tool": "t", "latency_ms": 0, **fields})


def test_from_server_flag() -> None:
    assert McpError(code="file_not_found", message="x").from_server is True
    assert McpError(code="tool_error", message="x").from_server is True
    assert McpError(code="tool_timeout", message="x").from_server is False


def test_server_error_prefers_explicit_billing_flag() -> None:
    explicit = json.dumps(
        {"error": {"code": "timeout", "message": "x", "details": {"usage": {"billing_uncertain": False}}}}
    )
    missing = json.dumps(
        {"error": {"code": "timeout", "message": "x", "details": {"usage": {"estimated_cost_inr": 0.1}}}}
    )
    no_usage = json.dumps({"error": {"code": "invalid_input", "message": "x"}})
    assert _server_error(explicit).uncertain is False
    assert _server_error(missing).uncertain is True
    assert _server_error(no_usage).uncertain is False


def test_server_error_reads_structured_content() -> None:
    structured = {"error": {"code": "upstream_error", "message": "chunk 3", "details": {"usage": {"cost": 0.08}}}}
    error = _server_error("Translation failed.", structured)
    assert (error.code, error.details["usage"]) == ("upstream_error", {"cost": 0.08})


def test_server_error_without_json_is_uncertain_tool_error() -> None:
    error = _server_error("boom")
    assert (error.code, error.message, error.uncertain, error.from_server) == ("tool_error", "boom", True, True)


async def test_fake_server_discovery_and_success(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        assert session.alive and session.instructions == "fake instructions"
        assert session.spec("echo") is not None and session.spec("nope") is None
        assert session.spec("echo").input_schema == {"type": "object"}
        result = await session.call("echo", {"x": 1})
        assert result.ok and result.data["echo"] == {"x": 1}
        assert result.usage == {"estimated_cost_inr": 0.2, "billing_uncertain": False}
        assert result.latency_ms >= 0
    assert session.alive is False
    assert "fake server started" in session.stderr_tail()


async def test_server_error_keeps_usage(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        result = await session.call("fail", {})
    assert not result.ok
    error = result.error
    assert (error.code, error.status, error.hint, error.retryable, error.uncertain) == (
        "upstream_error",
        502,
        "retry later",
        True,
        False,
    )
    assert result.usage == {"estimated_cost_inr": 0.08, "billing_uncertain": False}


async def test_plain_text_error(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        result = await session.call("fail_plain", {})
    assert (result.error.code, result.error.message, result.error.uncertain) == ("tool_error", "something broke", True)


async def test_text_json_success_and_text_only_protocol_error(
    run_settings: RunSettings, fake_command: list[str]
) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        from_text = await session.call("text_json", {})
        no_json = await session.call("text_only", {})
    assert from_text.ok and from_text.data == {"from": "text"}
    assert (no_json.ok, no_json.error.code, no_json.error.uncertain) == (False, "protocol_error", True)


async def test_unknown_tool_not_dispatched(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        result = await session.call("mystery", {})
    assert (result.error.code, result.error.uncertain) == ("unknown_tool", False)
    assert "echo" in result.error.message


async def test_no_time_left_is_not_uncertain(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        result = await session.call("echo", {}, deadline=monotonic() - 1)
    assert (result.error.code, result.error.uncertain) == ("tool_timeout", False)


async def test_hanging_call_times_out_as_uncertain(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        started = monotonic()
        result = await session.call("hang", {}, deadline=monotonic() + 1)
        assert monotonic() - started < 5
        assert (result.error.code, result.error.uncertain, result.error.retryable) == ("tool_timeout", True, True)
        assert session.alive
        assert (await session.call("echo", {"after": True})).ok


async def test_parallel_calls_return_their_own_results(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        results = await asyncio.gather(
            session.call("slow", {"seconds": 0.6, "tag": "first"}),
            session.call("slow", {"seconds": 0.1, "tag": "second"}),
            session.call("slow", {"seconds": 0.3, "tag": "third"}),
        )
    assert [result.data["tag"] for result in results] == ["first", "second", "third"]


async def test_crash_marks_session_unavailable(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        crashed = await session.call("crash", {}, deadline=monotonic() + 5)
        assert crashed.ok is False
        assert crashed.error.code in {"server_unavailable", "tool_timeout", "protocol_error"}
        assert crashed.error.uncertain is True


async def test_call_after_close_is_unavailable(run_settings: RunSettings, fake_command: list[str]) -> None:
    session = await McpSession(run_settings, no_keys_env(), command=fake_command).start()
    await session.aclose()
    await session.aclose()
    result = await session.call("echo", {})
    assert (result.error.code, result.error.uncertain) == ("server_unavailable", False)


async def test_double_start_rejected(run_settings: RunSettings, fake_command: list[str]) -> None:
    async with McpSession(run_settings, no_keys_env(), command=fake_command) as session:
        with pytest.raises(RuntimeError, match="already started"):
            await session.start()


async def test_server_that_exits_reports_stderr(run_settings: RunSettings) -> None:
    command = [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"]
    with pytest.raises(McpUnavailable, match="boom"):
        await McpSession(run_settings, no_keys_env(), command=command, connect_timeout_s=5).start()


async def test_silent_server_times_out(run_settings: RunSettings) -> None:
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = monotonic()
    with pytest.raises(McpUnavailable, match="could not be started"):
        await McpSession(run_settings, no_keys_env(), command=command, connect_timeout_s=1).start()
    assert monotonic() - started < 10


async def test_missing_executable_reports_unavailable(run_settings: RunSettings) -> None:
    with pytest.raises(McpUnavailable):
        await McpSession(run_settings, no_keys_env(), command=["/no/such/python"], connect_timeout_s=2).start()


async def test_real_server_without_keys(run_settings: RunSettings) -> None:
    async with McpSession(run_settings, no_keys_env()) as session:
        assert sorted(tool.name for tool in session.tools) == [
            "ocr_document",
            "synthesize_speech",
            "transcribe_audio",
            "translate_text",
        ]
        assert all(tool.input_schema.get("type") == "object" for tool in session.tools)
        missing = await session.call("ocr_document", {"file_path": "nope.pdf"})
        invalid = await session.call("translate_text", {"text": ""})
        no_key = await session.call("translate_text", {"text": "Hello", "target_language": "hi"})
    assert (missing.error.code, missing.error.status, missing.error.from_server) == ("file_not_found", 404, True)
    assert missing.usage is not None and missing.usage["estimated_cost_inr"] == 0.0
    assert invalid.error.code == "invalid_input" and invalid.error.uncertain is False
    assert no_key.error.code == "missing_api_key"
    assert run_settings.work_dir.joinpath("server.stderr.log").exists()
