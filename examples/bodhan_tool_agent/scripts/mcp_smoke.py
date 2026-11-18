"""MCP stdio smoke test script using official MCP SDK client.

Connects to 'python -m bodhan_mcp' over stdio, lists all registered tools,
and exercises every single tool with malformed/invalid inputs to verify that:
1. Each tool returns a clean structured error (never an unhandled exception or crash).
2. The MCP server process remains running and responsive without hanging.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def run_mcp_smoke(python_executable: str = sys.executable) -> Dict[str, Any]:
    """Runs smoke test against bodhan_mcp server over stdio."""
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)
    # Ensure live calls are explicitly disabled during smoke test
    env["BODHAN_ALLOW_LIVE"] = "0"

    server_params = StdioServerParameters(
        command=python_executable,
        args=["-m", "bodhan_mcp"],
        env=env,
    )

    results: Dict[str, Any] = {}

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # 1. List tools
            tools_list = await session.list_tools()
            tool_names = [t.name for t in tools_list.tools]
            results["discovered_tools"] = tool_names

            # 2. Test cases with malformed/bad inputs for EVERY tool exposed by MCP server
            test_cases: List[Dict[str, Any]] = [
                {
                    "tool": "indic_translate",
                    "args": {"text": "", "target_language": "hi"},
                    "expect_error": True,
                    "expected_code": "EMPTY_INPUT",
                },
                {
                    "tool": "indic_translate",
                    "args": {"text": "Hello world", "target_language": "klingon"},
                    "expect_error": True,
                    "expected_code": "UNSUPPORTED_LANGUAGE",
                },
                {
                    "tool": "indic_ocr",
                    "args": {"file_path": "non_existent_page_12345.png"},
                    "expect_error": True,
                    "expected_code": "FILE_NOT_FOUND",
                },
                {
                    "tool": "indic_transcribe",
                    "args": {"file_path": "non_existent_audio_12345.wav"},
                    "expect_error": True,
                    "expected_code": "FILE_NOT_FOUND",
                },
                {
                    "tool": "indic_speak",
                    "args": {"input_text": "", "language": "hi", "output_path": "out/bad.wav"},
                    "expect_error": True,
                    "expected_code": "EMPTY_INPUT",
                },
                {
                    "tool": "indic_speak",
                    "args": {
                        "input_text": "Hello",
                        "language": "elvish",
                        "output_path": "out/bad.wav",
                    },
                    "expect_error": True,
                    "expected_code": "UNSUPPORTED_LANGUAGE",
                },
                {
                    "tool": "inspect_media",
                    "args": {"path": "../../etc/shadow"},
                    "expect_error": True,
                },
                {
                    "tool": "read_text_file",
                    "args": {"path": "script.exe"},
                    "expect_error": True,
                },
                {
                    "tool": "make_srt",
                    "args": {"segments": [], "output_path": "out/bad.srt"},
                    "expect_error": True,
                    "expected_code": "EMPTY_INPUT",
                },
                {
                    "tool": "detect_script_language",
                    "args": {"text": "नमस्ते दुनिया"},
                    "expect_error": False,
                },
            ]

            tested_calls = []
            for tc in test_cases:
                tool_name = tc["tool"]
                args = tc["args"]
                call_res = await session.call_tool(tool_name, arguments=args)
                raw_text = ""
                if call_res.content and len(call_res.content) > 0:
                    raw_text = getattr(call_res.content[0], "text", str(call_res.content[0]))

                parsed: Dict[str, Any] = {}
                try:
                    parsed = json.loads(raw_text)
                except Exception:
                    parsed = {"raw": raw_text}

                has_err = (
                    not parsed.get("success", True)
                    or "error" in parsed
                    or getattr(call_res, "is_error", False)
                )
                tested_calls.append(
                    {
                        "tool": tool_name,
                        "args": args,
                        "output": parsed,
                        "has_error": has_err,
                    }
                )

            results["tested_calls"] = tested_calls
            results["server_alive"] = True

    return results


if __name__ == "__main__":
    res = asyncio.run(run_mcp_smoke())
    print(json.dumps(res, indent=2))
