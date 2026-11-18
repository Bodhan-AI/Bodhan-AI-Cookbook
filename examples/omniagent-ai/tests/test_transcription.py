
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

            test_cases = [
                {
                    "name": "Missing audio file",
                    "arguments": {
                        "audio_path": "data/missing.wav",
                        "language": "hi",
                    },
                    "expected_message": "Audio file not found",
                },
                {
                    "name": "Empty audio path",
                    "arguments": {
                        "audio_path": "",
                        "language": "hi",
                    },
                    "expected_message": "Audio file path cannot be empty",
                },
            ]

            for test in test_cases:
                result = await session.call_tool(
                    "transcribe_audio",
                    arguments=test["arguments"],
                )

                response = json.loads(result.content[0].text)

                assert response["status"] == "error"
                assert test["expected_message"] in response["message"]

                print(f"PASS: {test['name']}")

    print("\nAll transcription validation tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
