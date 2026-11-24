import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_standalone_stdio_server_accepts_official_mcp_client():
    recipe = Path(__file__).resolve().parents[1]
    source = recipe / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source) + os.pathsep + env.get("PYTHONPATH", "")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        cwd=recipe,
        env=env,
    )

    async def run():
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "bodhan_translate" in names
                assert "bodhan_ocr" in names

                result = await session.call_tool(
                    "bodhan_translate",
                    {"text": "Bonjour", "target_language": "fr"},
                )
                assert result.is_error is False
                assert "LANGUAGE_UNSUPPORTED" in result.content[0].text

    asyncio.run(run())
