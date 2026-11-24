
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    root = Path(__file__).resolve().parents[1]

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        cwd=str(root),
        env={"PYTHONPATH": str(root / "src")},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(
                "generate_speech",
                arguments={
                    "text": "Welcome to OmniAgent AI.",
                    "language": "en",
                    "output_path": str(
                        root / "out" / "artifacts" / "mcp_speech.wav"
                    ),
                },
            )

            assert not result.isError, result.content

            response = json.loads(result.content[0].text)

            print(json.dumps(response, indent=2))

            assert response["status"] == "success", response

            audio_path = Path(response["output_path"])

            assert audio_path.exists()
            assert audio_path.stat().st_size > 0

            print("\nPASS: Real speech generation through MCP!")


if __name__ == "__main__":
    asyncio.run(main())
