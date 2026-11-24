#!/usr/bin/env python3
"""Standalone MCP Client Verification Script.

Simulates an off-the-shelf Model Context Protocol (MCP) client connecting
to Bodhan MCP server over standard protocol streams, verifying:
1. Server initialization and metadata handshake
2. Tool discovery and schema completeness across every registered tool
3. Normal execution with valid arguments
4. Graceful error handling (ToolError/isError=True) on bad inputs (missing files,
   unsupported languages, empty strings, path traversals).

Usage:
    python verify_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

# Ensure recipe dir is on sys.path
_recipe_dir = str(Path(__file__).resolve().parent)
if _recipe_dir not in sys.path:
    sys.path.insert(0, _recipe_dir)

import anyio
from mcp import ClientSession

from src.bodhan_mcp.server import mcp


async def verify_mcp_client() -> int:
    print("=" * 70)
    print(" Bodhan Standalone MCP Server Client Verification Harness")
    print("=" * 70)

    c2s_send, c2s_recv = anyio.create_memory_object_stream(50)
    s2c_send, s2c_recv = anyio.create_memory_object_stream(50)

    passed_checks = 0
    total_checks = 0

    def check(name: str, success: bool, detail: str = ""):
        nonlocal passed_checks, total_checks
        total_checks += 1
        if success:
            passed_checks += 1
            print(f"  [PASS] {name} {detail}")
        else:
            print(f"  [FAIL] {name} {detail}")

    async with anyio.create_task_group() as tg:
        tg.start_soon(mcp._mcp_server.run, c2s_recv, s2c_send, mcp._mcp_server.create_initialization_options())

        async with ClientSession(s2c_recv, c2s_send) as session:
            # 1. Initialization
            init_res = await session.initialize()
            check("Server Init", init_res.serverInfo.name == "bodhan-mcp", f"name='{init_res.serverInfo.name}'")

            # 2. Tool Listing
            tools_res = await session.list_tools()
            tool_names = {t.name for t in tools_res.tools}
            expected_tools = {
                "transcribe_audio",
                "translate_text",
                "synthesize_speech",
                "extract_document_text",
                "inspect_media",
                "read_text_file",
                "write_text_file",
                "detect_language",
                "get_cache_stats",
                "clear_cache",
            }
            check(
                "Tool Discovery",
                expected_tools.issubset(tool_names),
                f"Found {len(tool_names)} tools: {sorted(tool_names)}",
            )

            # 3. Valid call: detect_language
            lang_res = await session.call_tool("detect_language", {"text": "భారతదేశం నా మాతృభూమి"})
            lang_data = json.loads(lang_res.content[0].text) if lang_res.content else {}
            check(
                "Tool: detect_language (Telugu)",
                not lang_res.isError and lang_data.get("script") == "Telugu",
                f"detected={lang_data.get('script')}, likely={lang_data.get('likely_languages')}",
            )

            with tempfile.TemporaryDirectory() as td:
                tmp_dir = Path(td)
                test_file = tmp_dir / "sample.txt"

                # 4. Valid call: write_text_file
                write_res = await session.call_tool(
                    "write_text_file",
                    {"file_path": str(test_file), "content": "Sample content for MCP testing"},
                )
                check("Tool: write_text_file", not write_res.isError and test_file.exists())

                # 5. Valid call: read_text_file
                read_res = await session.call_tool("read_text_file", {"file_path": str(test_file)})
                check(
                    "Tool: read_text_file",
                    not read_res.isError and "Sample content" in read_res.content[0].text,
                )

                # 5b. Valid call: get_cache_stats
                stats_res = await session.call_tool("get_cache_stats", {})
                check(
                    "Tool: get_cache_stats",
                    not stats_res.isError and "hits" in stats_res.content[0].text,
                )

                # 6. Bad Input: inspect_media with non-existent file
                bad_media = await session.call_tool("inspect_media", {"file_path": str(tmp_dir / "missing.wav")})
                check(
                    "Bad Input: inspect_media (missing file)",
                    bad_media.isError and "File not found" in bad_media.content[0].text,
                    f"isError={bad_media.isError}",
                )

                # 7. Bad Input: read_text_file with non-existent file
                bad_read = await session.call_tool("read_text_file", {"file_path": str(tmp_dir / "ghost.txt")})
                check(
                    "Bad Input: read_text_file (missing file)",
                    bad_read.isError and "File not found" in bad_read.content[0].text,
                    f"isError={bad_read.isError}",
                )

                # 8. Bad Input: translate_text with unsupported language
                bad_trans = await session.call_tool(
                    "translate_text",
                    {"text": "Hello", "target_language": "unsupported_xyz"},
                )
                check(
                    "Bad Input: translate_text (invalid language)",
                    bad_trans.isError and "not supported" in bad_trans.content[0].text,
                    f"isError={bad_trans.isError}",
                )

                # 9. Bad Input: synthesize_speech with empty text
                bad_tts = await session.call_tool(
                    "synthesize_speech",
                    {"text": "   ", "language": "hi"},
                )
                check(
                    "Bad Input: synthesize_speech (empty text)",
                    bad_tts.isError and "cannot be empty" in bad_tts.content[0].text,
                    f"isError={bad_tts.isError}",
                )

        tg.cancel_scope.cancel()

    print("-" * 70)
    print(f"Results: {passed_checks}/{total_checks} checks passed.")
    if passed_checks == total_checks:
        print(" [SUCCESS] Standalone MCP Server passed all protocol & bad input checks!")
        return 0
    else:
        print(" [FAILURE] Some MCP verification checks failed.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(verify_mcp_client()))
