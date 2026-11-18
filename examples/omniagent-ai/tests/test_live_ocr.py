
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
                "extract_text",
                arguments={
                    "file_path": str(
                        root / "data" / "sample_ocr.png"
                    ),
                    "language": "hi",
                },
            )

            assert not result.isError, result.content

            response = json.loads(result.content[0].text)

            print(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    indent=2
                )
            )

            assert response["status"] == "success", response
            assert response["extracted_text"].strip()

            expected = "नमस्ते भारत"
            actual = response["extracted_text"].strip()

            assert actual == expected, (
                f"Expected: {expected}, got: {actual}"
            )

            print("\nPASS: Real OCR through MCP!")
            print("PASS: Extracted Hindi text matches expected text!")


if __name__ == "__main__":
    asyncio.run(main())
