"""Integration tests for Bodhan FastMCP server tools and resources."""

from __future__ import annotations

import json
from bodhan_mcp.resources import (
    get_languages_resource,
    get_pricing_resource,
    get_voices_resource,
)
from bodhan_mcp.server import mcp_server


def test_mcp_server_metadata():
    assert mcp_server.name == "Bodhan MCP Tool Server"


def test_mcp_resources_contents():
    # Languages
    langs_json = get_languages_resource()
    langs_data = json.loads(langs_json)
    assert "all_languages" in langs_data
    assert "hi" in langs_data["capabilities"]["transcribe"]

    # Voices
    voices_json = get_voices_resource()
    voices_data = json.loads(voices_json)
    assert "hi" in voices_data
    assert "Kavya" in voices_data["hi"]["female"]

    # Pricing
    pricing_json = get_pricing_resource()
    pricing_data = json.loads(pricing_json)
    assert pricing_data["currency"] == "INR"
    assert "indic-transcribe" in pricing_data["models"]


def test_mcp_tools_exposed():
    # FastMCP holds list of tools
    tools = mcp_server._tool_manager.list_tools()
    tool_names = {t.name for t in tools}
    assert "transcribe_audio" in tool_names
    assert "translate_text" in tool_names
    assert "synthesize_speech" in tool_names
    assert "extract_text_from_document" in tool_names
