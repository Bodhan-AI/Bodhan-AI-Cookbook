"""Drive `python -m bodhan_mcp` over stdio with the official MCP client SDK, as any third-party client would."""
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def parse(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_server_lists_tools_and_returns_structured_errors(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"],
                                   env={"BODHAN_WORKDIR": str(tmp_path), "PATH": "/usr/bin:/bin", "BODHAN_CACHE_DIR": str(tmp_path / "c")})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"} <= set(tools)
            assert all(t.description and t.inputSchema for t in tools.values())

            ok = parse(await s.call_tool("check_language_support", {"language": "Telugu"}))
            assert ok["ok"] and ok["supported"]

            bad = parse(await s.call_tool("inspect_media", {"path": "/etc/passwd"}))
            assert not bad["ok"] and bad["error"]["code"] == "path_outside_workspace"

            nokey = parse(await s.call_tool("translate_text", {"target_language": "hi", "text": "Hello."}))
            assert not nokey["ok"] and nokey["error"]["code"] == "missing_api_key"

            res = await s.read_resource("bodhan://voices")
            assert "Kavya" in res.contents[0].text
            # the server survived every bad call
            assert parse(await s.call_tool("list_voices", {"language": "ta"}))["ok"]
