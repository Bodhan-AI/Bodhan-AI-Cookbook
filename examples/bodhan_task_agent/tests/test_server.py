from __future__ import annotations

import json
from pathlib import Path

import mcp_types as types
import pytest
from conftest import make_settings
from mcp.shared.exceptions import MCPError

from bodhan_mcp.core.registry import ToolRegistry
from bodhan_mcp.server import build_server
from bodhan_mcp.tools.ocr import OcrTool
from bodhan_mcp.tools.synthesize import SynthesizeTool
from bodhan_mcp.tools.transcribe import TranscribeTool
from bodhan_mcp.tools.translate import TranslateTool

EXPECTED_TOOLS = {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"}


def _registry(tmp_path: Path) -> ToolRegistry:
    settings = make_settings(tmp_path)
    tools = [cls(None, settings) for cls in (TranscribeTool, TranslateTool, SynthesizeTool, OcrTool)]  # type: ignore[arg-type]
    return ToolRegistry(tools)


def test_build_server_creates_folders(tmp_path: Path) -> None:
    settings = make_settings(tmp_path / "fresh")
    assert build_server(settings) is not None
    assert settings.input_dir.is_dir() and settings.output_dir.is_dir()


async def test_lists_four_tools_with_schemas(tmp_path: Path) -> None:
    result = await _registry(tmp_path).list_tools(None, None)
    assert {tool.name for tool in result.tools} == EXPECTED_TOOLS
    for tool in result.tools:
        assert tool.description and tool.input_schema["type"] == "object" and tool.output_schema


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("translate_text", {"text": "hi", "target_language": "fr"}),
        ("synthesize_speech", {"text": "x", "language": "hi", "voice": "kavya"}),
        ("ocr_document", {"file_path": "a.pdf", "pages": "3-1"}),
        ("transcribe_audio", {"audio_path": "a.wav", "unknown": 1}),
        ("transcribe_audio", {}),
    ],
)
async def test_bad_arguments_return_structured_errors(tmp_path: Path, name: str, arguments: dict) -> None:
    result = await _registry(tmp_path).call_tool(None, types.CallToolRequestParams(name=name, arguments=arguments))
    assert result.is_error and result.structured_content is None
    error = json.loads(result.content[0].text)["error"]
    assert error["code"] == "invalid_input" and error["status"] == 400 and error["details"]["errors"]


async def test_unknown_tool_is_a_protocol_error(tmp_path: Path) -> None:
    with pytest.raises(MCPError):
        await _registry(tmp_path).call_tool(None, types.CallToolRequestParams(name="nope", arguments={}))


async def test_missing_file_is_a_tool_error_not_a_crash(tmp_path: Path) -> None:
    params = types.CallToolRequestParams(name="ocr_document", arguments={"file_path": "missing.png"})
    result = await _registry(tmp_path).call_tool(None, params)
    assert result.is_error and json.loads(result.content[0].text)["error"]["code"] == "file_not_found"
