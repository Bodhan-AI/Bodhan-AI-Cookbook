"""The MCP server on its own, over stdio, driven by the official MCP Python client."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_stdio_server_lists_tools_and_returns_structured_errors(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "BODHAN_WORK_DIR": str(tmp_path), "BODHAN_API_KEY": "unused"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env, cwd=str(tmp_path))

    async def session_run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}
                described = all(t.description and t.inputSchema.get("type") == "object" for t in listed.tools)
                ok = await session.call_tool("list_languages", {"capability": "text_to_speech"})
                bad = await session.call_tool("transcribe_audio", {"path": str(tmp_path / "missing.wav")})
                wrong = await session.call_tool("translate_text", {"target_language": "fr", "text": "Hello"})
                return names, described, ok, bad, wrong

    names, described, ok, bad, wrong = asyncio.run(session_run())
    assert {"transcribe_audio", "translate_text", "speak_text", "ocr_document", "make_subtitles", "identify_audio_language", "read_text"} <= names
    assert described
    assert json.loads(ok.content[0].text)["ok"] is True
    assert json.loads(bad.content[0].text)["error"]["code"] == "file_not_found"
    assert json.loads(wrong.content[0].text)["error"]["code"] == "unsupported_language"
