"""Server tests: tool registration, error format, and a real stdio round trip."""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import load_settings
from bodhan_mcp.server import build_server
from bodhan_mcp.toolkit import Toolkit

EXPECTED = {"language_support", "detect_text_language", "inspect_file", "transcribe_audio",
            "translate_text", "speak_text", "verify_speech", "ocr_document", "write_subtitles", "get_usage"}


def test_all_tools_registered_with_descriptions(tmp_path):
    tk = Toolkit(BodhanClient(load_settings({})), tmp_path)
    tools = asyncio.run(build_server(tk).list_tools())
    assert {t.name for t in tools} == EXPECTED
    assert all(t.description and len(t.description) > 30 for t in tools)  # useful to a model


def test_stdio_round_trip_with_structured_error(tmp_path):
    env = {**os.environ, "BODHAN_API_KEY": "dummy", "BODHAN_OUT_DIR": str(tmp_path / "out")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                ok = await session.call_tool("language_support", {"language": "Bhojpuri"})
                bad = await session.call_tool("transcribe_audio", {"path": "/no/such/file.wav"})
                return names, ok, bad

    names, ok, bad = asyncio.run(run())
    assert names == EXPECTED
    assert ok.is_error is False
    assert json.loads(ok.content[0].text)["translate"] is False
    assert bad.is_error is True
    text = bad.content[0].text  # the library prefixes "Error executing tool X: " before our JSON
    err = json.loads(text[text.index("{"):])["error"]  # structured: code + hint, not prose
    assert err["code"] == "file_not_found" and err["hint"]