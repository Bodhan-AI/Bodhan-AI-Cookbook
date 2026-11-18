
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

    # Create temporary test files
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)

    valid_image = data_dir / "test_ocr.png"
    valid_image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a"
            "0000000d494844520000000100000001"
            "08060000001f15c489"
            "0000000b49444154789c6360000200"
            "00050001a5f64540"
            "0000000049454e44ae426082"
        )
    )

    empty_image = data_dir / "empty_ocr.png"
    empty_image.write_bytes(b"")

    test_cases = [
        {
            "name": "Valid image file",
            "arguments": {
                "file_path": str(valid_image),
                "language": "hi",
            },
            "expected_status": "pending_api_integration",
        },
        {
            "name": "Missing file",
            "arguments": {
                "file_path": str(data_dir / "missing_ocr.png"),
            },
            "expected_status": "error",
        },
        {
            "name": "Empty file path",
            "arguments": {
                "file_path": "",
            },
            "expected_status": "error",
        },
        {
            "name": "Unsupported file format",
            "arguments": {
                "file_path": str(root / "src" / "bodhan_mcp" / "server.py"),
            },
            "expected_status": "error",
        },
        {
            "name": "Empty image file",
            "arguments": {
                "file_path": str(empty_image),
            },
            "expected_status": "error",
        },
    ]

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                for test in test_cases:
                    result = await session.call_tool(
                        "extract_text",
                        arguments=test["arguments"],
                    )

                    assert not result.isError, (
                        f"MCP error: {test['name']}"
                    )

                    response = json.loads(result.content[0].text)

                    assert response["status"] == test["expected_status"], (
                        f"FAILED: {test['name']} - {response}"
                    )

                    print(f"PASS: {test['name']}")

        print("\nAll OCR validation tests passed!")

    finally:
        valid_image.unlink(missing_ok=True)
        empty_image.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
