"""The server runs on its own over stdio and an MCP client can use it without the agent."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _session_roundtrip(tmp_path):
    env = {**os.environ, "BODHAN_WORK_DIR": str(tmp_path), "BODHAN_API_KEY": "", "BODHAN_TRANSLATE_KEY": ""}
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            bad = await s.call_tool("inspect_media", {"path": "/no/such/file.wav"})
            invalid = await s.call_tool("translate_text", {"target_language": "hi"})  # neither text nor text_path
            missing_arg = await s.call_tool("transcribe_audio", {})
            no_key = await s.call_tool("translate_text", {"target_language": "hi", "text": "Hello"})
            voices = await s.read_resource("bodhan://voices")
            return tools, bad, invalid, missing_arg, no_key, voices


def test_stdio_server_lists_typed_tools_and_returns_structured_errors(tmp_path):
    tools, bad, invalid, missing_arg, no_key, voices = asyncio.run(_session_roundtrip(tmp_path))
    assert {"inspect_media", "transcribe_audio", "translate_text", "ocr_document", "synthesize_speech", "verify_audio"} <= set(tools)
    for t in tools.values():
        assert t.description and len(t.description) > 40 and t.inputSchema["properties"] is not None and t.outputSchema
    assert bad.structuredContent["error"]["code"] == "file_not_found"
    assert invalid.structuredContent["error"]["code"] == "invalid_input"
    assert missing_arg.isError  # schema violation is rejected by the protocol layer, server stays alive
    assert no_key.structuredContent["error"]["code"] == "missing_api_key"
    assert "Anitha" in voices.contents[0].text and json.loads(voices.contents[0].text)["styles"]
