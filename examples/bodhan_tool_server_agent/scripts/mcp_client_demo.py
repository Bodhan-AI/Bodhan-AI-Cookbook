"""Talk to `python -m bodhan_mcp` the way any MCP client would: list the tools, then trigger a bad-input error.
No credits, no network. Run: uv run python scripts/mcp_client_demo.py"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        print(f"{len(tools)} tools over stdio:")
        for t in tools:
            print(f"  {t.name:20} inputs: {', '.join(t.inputSchema.get('properties', {})) or '-':45.45} typed output: {bool(t.outputSchema)}")
        res = await s.call_tool("read_text_file", {"path": "/nope.txt"})
        err = json.loads(res.content[0].text[res.content[0].text.index("{"):])
        print("\nbad input, read_text_file('/nope.txt'):")
        print(f"  isError={res.isError} code={err['code']}\n  message: {err['message']}\n  hint: {err['hint']}")


asyncio.run(main())
