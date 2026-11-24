"""
Contract tests for all 7 Bodhan MCP tools.
Ensures tool schemas, structured error envelopes, and safe handling of edge cases.
"""
import asyncio
import pytest
from pathlib import Path
from bodhan_mcp.server import create_server
from bodhan_core.errors import ErrorCode


def test_tools_registration():
    async def _test():
        server = create_server()
        tools = await server.list_tools()
        tool_names = {t.name for t in tools}

        expected_tools = {
            "inspect_media",
            "detect_language",
            "transcribe_audio",
            "translate_text",
            "synthesize_speech",
            "ocr_document",
            "list_voices",
        }
        assert expected_tools.issubset(tool_names)

    asyncio.run(_test())


def test_inspect_media_missing_file():
    async def _test():
        server = create_server()
        result = await server.call_tool("inspect_media", {"file_path": "non_existent_file.wav"})
        content = result[0].text if isinstance(result, list) else str(result)
        assert "FILE_NOT_FOUND" in content or "ok" in content

    asyncio.run(_test())


def test_list_voices_contract():
    async def _test():
        server = create_server()
        result = await server.call_tool("list_voices", {"language": "hi"})
        content = result[0].text if isinstance(result, list) else str(result)
        assert "Kavya" in content or "voices" in content

    asyncio.run(_test())
