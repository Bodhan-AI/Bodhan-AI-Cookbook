
import asyncio
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
                    "name": "Valid translation request",
                    "arguments": {
                        "text": "Hello, how are you?",
                        "source_lang": "en",
                        "target_lang": "hi",
                    },
                    "expected_status": "pending_api_integration",
                },
                {
                    "name": "Unsupported target language",
                    "arguments": {
                        "text": "Hello, how are you?",
                        "source_lang": "en",
                        "target_lang": "xyz",
                    },
                    "expected_status": "error",
                },
                {
                    "name": "Empty text",
                    "arguments": {
                        "text": "",
                        "source_lang": "en",
                        "target_lang": "hi",
                    },
                    "expected_status": "error",
                },
                {
                    "name": "Same source and target language",
                    "arguments": {
                        "text": "Hello",
                        "source_lang": "en",
                        "target_lang": "en",
                    },
                    "expected_status": "error",
                },
            ]

            import json

            for test in test_cases:
                print(f"\n--- {test['name']} ---")

                result = await session.call_tool(
                    "translate_text",
                    arguments=test["arguments"],
                )

                response = json.loads(result.content[0].text)
                print(json.dumps(response, indent=2))

                assert response["status"] == test["expected_status"]
                print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
