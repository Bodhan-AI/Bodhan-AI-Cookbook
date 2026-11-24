"""Tests for FastMCP server registration, tool surface, and stdio execution (Slice 6)."""

import json
from pathlib import Path

import pytest

from bodhan_mcp.server import mcp


@pytest.mark.asyncio
async def test_all_eight_tools_registered():
    """Verify all eight tools per SPEC §4 and TOOL_DESIGN.md are registered on FastMCP."""
    tool_names = set(mcp._tool_manager._tools.keys())  # FastMCP internal tool registry

    expected_tools = {
        "transcribe_audio",
        "translate_text",
        "synthesize_speech",
        "ocr_page",
        "ocr_document",
        "inspect_media",
        "chunk_audio",
        "get_voice_library",
    }
    missing = expected_tools - tool_names
    assert not missing, f"Missing registered tools on FastMCP: {missing}"


def test_resource_registered():
    """Verify voice library resource is registered at voices://library."""
    resource_uris = set(mcp._resource_manager._resources.keys())
    assert "voices://library" in resource_uris

    # Fetch resource content
    res_func = mcp._resource_manager._resources["voices://library"].fn
    data = json.loads(res_func())
    assert "hi" in data
    assert "ta" in data


@pytest.mark.asyncio
async def test_server_inspect_media_bad_input(tmp_path: Path):
    """Verify tool execution with bad input returns structured error via FastMCP."""
    inspect_fn = mcp._tool_manager._tools["inspect_media"].fn
    with pytest.raises(ValueError) as exc_info:
        inspect_fn(path=str(tmp_path / "non_existent.pdf"))

    # The error message should be JSON containing code FILE_NOT_FOUND
    error_payload = json.loads(str(exc_info.value))
    assert error_payload["code"] == "FILE_NOT_FOUND"


@pytest.mark.asyncio
async def test_server_chunk_audio_bad_input(tmp_path: Path):
    """Verify chunk_audio on bad input raises structured error."""
    chunk_fn = mcp._tool_manager._tools["chunk_audio"].fn
    with pytest.raises(ValueError) as exc_info:
        chunk_fn(path=str(tmp_path / "bad.wav"))

    error_payload = json.loads(str(exc_info.value))
    assert error_payload["code"] == "FILE_NOT_FOUND"
