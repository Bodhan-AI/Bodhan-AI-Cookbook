from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from bodhan_mcp.config import Settings
from bodhan_mcp.server import create_server
from bodhan_mcp.tools import ToolService


def test_stdio_server_discovers_tools_and_handles_invalid_path(tmp_path: Path) -> None:
    async def exercise_client() -> None:
        environment = os.environ.copy()
        environment["BODHAN_OUTPUT_DIR"] = str(tmp_path / "server-output")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "bodhan_mcp"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert names == {
                    "transcribe_audio",
                    "translate_text",
                    "translate_subtitles",
                    "generate_speech",
                    "ocr_document",
                }
                result = await session.call_tool(
                    "ocr_document",
                    {"file_path": str(tmp_path / "missing.png")},
                )
                assert not result.isError
                assert result.structuredContent["ok"] is False
                assert result.structuredContent["error"]["code"] == "missing_input"

    asyncio.run(exercise_client())


def test_mcp_client_invokes_a_valid_tool_without_live_api_calls(tmp_path: Path) -> None:
    service = ToolService(
        Settings(translate_key="unit-test-key"),
        input_roots=(tmp_path,),
        output_dir=tmp_path / "server-output",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "नमस्ते"}}], "usage": {"completion_tokens": 2}},
                )
            )
        ),
    )

    async def exercise_client() -> None:
        async with create_connected_server_and_client_session(create_server(service)) as session:
            await session.initialize()
            result = await session.call_tool(
                "translate_text",
                {"text": "Hello.", "target_language": "hi"},
            )
            assert not result.isError
            assert result.structuredContent["ok"] is True
            assert result.structuredContent["estimated_cost_inr"] >= 0
            assert Path(result.structuredContent["data"]["artifact_path"]).is_file()

    try:
        asyncio.run(exercise_client())
    finally:
        service.close()
