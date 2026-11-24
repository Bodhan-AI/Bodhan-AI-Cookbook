"""Real standalone stdio server exercised by official MCP SDK. No paid API calls."""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path("outputs/mcp_client_demo")
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "source.txt").write_text("Synthetic English source for MCP protocol demo.")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        env={
            **os.environ,
            "BODHAN_INPUT_ROOT": str(inputs.resolve()),
            "BODHAN_OUTPUT_ROOT": str((root / "sessions").resolve()),
        },
    )
    records = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            records.append({"real_stdio_tools": [tool.name for tool in tools.tools]})
            result = await client.call_tool("list_files", {})
            files = json.loads(result.content[0].text)
            records.append({"list_files": files})
            ref = files["files"][0]["ref"]
            for name, args in (
                ("translate", {"source": ref, "target_language": "fr"}),
                ("read_text", {"source": "../../etc/passwd"}),
                ("read_text", {"source": ref}),
            ):
                result = await client.call_tool(name, args)
                records.append({"tool": name, "result": json.loads(result.content[0].text)})
    record = {"kind": "real_mcp_protocol_not_model_quality", "records": records}
    (root / "record.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
