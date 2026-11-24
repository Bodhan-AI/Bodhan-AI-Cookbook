"""The server runs on its own over stdio and answers a real MCP client (free tools only, no network)."""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_lists_tools_and_returns_structured_errors(tmp_path):
    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"],
                                       env={**os.environ, "BODHAN_CACHE_DIR": str(tmp_path / "c"),
                                            "BODHAN_WORK_DIR": str(tmp_path / "w")})
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"} <= tools.keys()
            assert all(t.description and t.inputSchema["type"] == "object" for t in tools.values())
            assert "target_language" in tools["translate_text"].inputSchema["required"]

            ok = await s.call_tool("list_voices", {"language": "Tamil"})
            assert not ok.isError and json.loads(ok.content[0].text)["voices"]["female"] == ["Anitha"]

            bad = await s.call_tool("ocr_document", {"path": str(tmp_path / "missing.pdf")})
            assert bad.isError and '"file_not_found"' in bad.content[0].text

            unsupported = await s.call_tool("translate_text", {"target_language": "fr", "text": "hi"})
            assert unsupported.isError and "unsupported_language" in unsupported.content[0].text

            wrong_type = await s.call_tool("read_text", {"path": str(tmp_path), "max_chars": "lots"})
            assert wrong_type.isError

            res = await s.list_resources()
            assert any(str(x.uri) == "bodhan://voices" for x in res.resources)

    asyncio.run(asyncio.wait_for(go(), 60))
