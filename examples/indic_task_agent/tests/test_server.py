"""The MCP server through a real MCP client: schemas, resources, and bad inputs that must never crash it."""

import json
import sys

import pytest
from mcp import Client

from bodhan_agent.llm import ToolBox, stdio_server
from bodhan_mcp import server
from bodhan_mcp.tools import Services
from conftest import make_settings

EXPECTED_TOOLS = {"inspect_media", "detect_language", "read_text", "transcribe_audio", "translate_text",
                  "transliterate_text", "synthesize_speech", "ocr_document", "make_subtitles", "estimate_cost"}

BAD_CALLS = [
    ("inspect_media", {"path": "does/not/exist.wav"}),
    ("inspect_media", {}),
    ("detect_language", {}),
    ("read_text", {"path": "pyproject.toml", "max_chars": "lots"}),
    ("transcribe_audio", {"path": "pyproject.toml"}),
    ("transcribe_audio", {"path": "x.wav", "language": "klingon"}),
    ("translate_text", {"target_language": "fr", "text": "hello"}),
    ("translate_text", {"target_language": 42}),
    ("translate_text", {"target_language": "hi", "path": "a", "text": "b"}),
    ("transliterate_text", {"language": "hi", "target_script": "greek", "text": "x"}),
    ("synthesize_speech", {"language": "ta", "text": "English text"}),
    ("synthesize_speech", {"language": "ta", "text": "x", "voice": "Nobody"}),
    ("synthesize_speech", {"language": "xx", "text": "x"}),
    ("ocr_document", {"path": "pyproject.toml"}),
    ("ocr_document", {"path": "pyproject.toml", "pages": "abc"}),
    ("make_subtitles", {"segments_path": "pyproject.toml"}),
    ("estimate_cost", {"operation": "teleport"}),
    ("estimate_cost", {"operation": "speak"}),
]


@pytest.fixture
def in_process_server(tmp_path):
    server._services = Services.create(make_settings(tmp_path))
    yield server.mcp
    server._services = None


async def test_tools_have_schemas_and_descriptions(in_process_server):
    async with Client(in_process_server) as client:
        tools = (await client.list_tools()).tools
        assert {t.name for t in tools} == EXPECTED_TOOLS
        for tool in tools:
            assert len(tool.description or "") > 80, tool.name
            assert tool.input_schema["type"] == "object" and tool.output_schema
            for name, prop in tool.input_schema.get("properties", {}).items():
                assert prop.get("description"), f"{tool.name}.{name} has no description"
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        assert {"bodhan://languages", "bodhan://voices"} <= resources


@pytest.mark.parametrize("name,args", BAD_CALLS)
async def test_bad_inputs_return_structured_errors(in_process_server, name, args):
    async with Client(in_process_server) as client:
        result = await client.call_tool(name, args)
        assert result.is_error
        text = "".join(c.text for c in result.content)
        if "{" in text and '"error"' in text:
            body = json.loads(text[text.index("{"):])
            assert body["error"]["code"] and body["error"]["message"]
        # The server is still alive after every bad call.
        assert (await client.list_tools()).tools


@pytest.mark.skipif(sys.platform == "win32" and not sys.executable, reason="no interpreter")
async def test_server_runs_standalone_over_stdio(tmp_path):
    async with ToolBox(stdio_server(tmp_path / "work", tmp_path / "cache")) as box:
        assert EXPECTED_TOOLS <= box.names
        missing = await box.call("inspect_media", {"path": str(tmp_path / "missing.wav")})
        assert not missing.ok and missing.error["code"] == "FILE_NOT_FOUND"
        est = await box.call("estimate_cost", {"operation": "ocr", "pages": 8})
        assert est.ok and est.data["meta"]["estimate_inr"] == pytest.approx(1.6)
