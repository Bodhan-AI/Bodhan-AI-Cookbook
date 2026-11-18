"""Start `python -m bodhan_mcp` as a subprocess and talk to it over stdio like any MCP client."""

import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


async def test_stdio_server_lists_and_calls_tools():
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert {"supported_languages", "list_voices"} <= tools

        bad = _payload(await session.call_tool("list_voices", {"language": "French"}))
        assert bad["ok"] is False and bad["error"]["code"] == "UNSUPPORTED_LANGUAGE"

        resources = {str(r.uri) for r in (await session.list_resources()).resources}
        assert "bodhan://voices" in resources
