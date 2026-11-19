"""Tests for MCP server tool registration, schemas, and descriptions."""
from __future__ import annotations

import pytest
from bodhan_mcp.server import mcp


@pytest.mark.asyncio
async def test_tool_registration():
    """All required Bodhan tools are registered on the FastMCP server."""
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}

    expected_tools = {
        "transcribe_audio",
        "translate_text",
        "synthesize_speech",
        "ocr_document",
        "inspect_media",
        "detect_script",
        "build_srt",
        "split_audio",
        "concat_audio",
    }
    missing = expected_tools - tool_names
    assert not missing, f"Missing registered tools: {missing}"


@pytest.mark.asyncio
async def test_tool_descriptions_and_costs():
    """Tools have clear descriptions detailing purpose and cost."""
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}

    for name in ["transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"]:
        tool = tool_map[name]
        assert tool.description, f"Tool {name} lacks a description"
        desc_lower = tool.description.lower()
        # Ensure cost is explicitly documented
        assert "₹" in tool.description or "cost" in desc_lower, (
            f"Tool {name} description does not mention cost in INR: {tool.description}"
        )


@pytest.mark.asyncio
async def test_no_inline_base64_in_tool_schemas():
    """Media plumbing guardrail: no tool accepts inline base64 media."""
    tools = await mcp.list_tools()

    for tool in tools:
        params = getattr(tool, "parameters", {})
        props = params.get("properties", {}) if isinstance(params, dict) else {}
        for prop_name, prop_schema in props.items():
            assert "base64" not in prop_name.lower(), (
                f"Tool {tool.name} parameter {prop_name} contains base64 in name!"
            )
            desc = prop_schema.get("description", "").lower() if isinstance(prop_schema, dict) else ""
            assert "base64 image" not in desc and "base64 audio" not in desc, (
                f"Tool {tool.name} parameter {prop_name} specifies base64 media: {desc}"
            )


@pytest.mark.asyncio
async def test_required_fields_and_schemas():
    """Tools have proper schemas and declare required inputs."""
    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}

    # transcribe_audio requires file_path
    transcribe = tool_map["transcribe_audio"]
    assert "file_path" in transcribe.parameters["properties"]
    assert "file_path" in transcribe.parameters.get("required", [])

    # synthesize_speech requires text, language, voice
    tts = tool_map["synthesize_speech"]
    assert "text" in tts.parameters["properties"]
    assert "language" in tts.parameters["properties"]
    assert "voice" in tts.parameters["properties"]
    assert set(["text", "language", "voice"]).issubset(set(tts.parameters.get("required", [])))
