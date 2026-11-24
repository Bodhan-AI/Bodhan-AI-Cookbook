
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

    test_cases = [
        {
            "name": "Valid TTS request",
            "arguments": {
                "text": "नमस्ते",
                "language": "hi",
                "output_path": "out/artifacts/speech.wav",
            },
            "expected_status": "pending_api_integration",
        },
        {
            "name": "Empty text",
            "arguments": {
                "text": "",
                "language": "hi",
            },
            "expected_status": "error",
        },
        {
            "name": "Unsupported language",
            "arguments": {
                "text": "Hello",
                "language": "xyz",
            },
            "expected_status": "error",
        },
        {
            "name": "Invalid output format",
            "arguments": {
                "text": "नमस्ते",
                "language": "hi",
                "output_path": "out/artifacts/speech.txt",
            },
            "expected_status": "error",
        },
        {
            "name": "Empty output path",
            "arguments": {
                "text": "नमस्ते",
                "language": "hi",
                "output_path": "",
            },
            "expected_status": "error",
        },
    ]

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            for test in test_cases:
                result = await session.call_tool(
                    "generate_speech",
                    arguments=test["arguments"],
                )

                assert not result.isError, (
                    f"MCP error in {test['name']}: {result.content}"
                )

                response = json.loads(result.content[0].text)

                assert response["status"] == test["expected_status"], (
                    f"Failed: {test['name']} - {response}"
                )

                print(f"PASS: {test['name']}")

    print("\nAll TTS validation tests passed!")


if __name__ == "__main__":
    asyncio.run(main())
