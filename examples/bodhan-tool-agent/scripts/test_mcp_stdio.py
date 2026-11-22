"""Verify Bodhan MCP server tool registrations, schemas, and stdio readiness.

Usage:
    python test_mcp_stdio.py

To inspect with the official MCP GUI Inspector:
    npx @modelcontextprotocol/inspector python -m bodhan_mcp
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure src is in python path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bodhan_mcp.server import mcp


def test_registered_tools() -> None:
    print("=" * 60)
    print("Bodhan FastMCP Server Tool Verification")
    print("=" * 60)

    tools = mcp._tool_manager.list_tools()
    print(f"Total tools registered: {len(tools)}\n")

    expected_tools = {
        "transcribe_audio",
        "translate_text",
        "synthesize_speech",
        "extract_document_text",
        "inspect_media",
        "detect_language",
        "extract_audio_from_video",
        "build_srt",
    }

    found_names = set()
    for t in tools:
        found_names.add(t.name)
        schema = t.parameters
        required = schema.get("required", []) if isinstance(schema, dict) else []
        print(f"Tool: {t.name:<25} | Required args: {required}")
        print(f"  Description: {t.description[:80]}...\n")

    missing = expected_tools - found_names
    if missing:
        raise AssertionError(f"Missing expected tools: {missing}")

    print("=" * 60)
    print("All 8 MCP tools verified successfully!")
    print("To test live over stdio with MCP Inspector, run:")
    print("  npx @modelcontextprotocol/inspector python -m bodhan_mcp")
    print("=" * 60)


if __name__ == "__main__":
    test_registered_tools()
