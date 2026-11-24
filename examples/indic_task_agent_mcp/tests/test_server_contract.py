"""The MCP server as a third-party client sees it: schemas, descriptions and structured errors on bad input."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from bodhan_mcp import server


def connect(ctx):
    server.set_context(ctx)
    return create_connected_server_and_client_session(server.mcp._mcp_server)


async def test_tools_are_described_and_typed(ctx):
    async with connect(ctx) as session:
        listed = (await session.list_tools()).tools
    names = {t.name for t in listed}
    assert names == {"inspect_file", "transcribe_audio", "translate_text", "translate_segments", "synthesize_speech",
                     "ocr_document", "write_subtitles", "read_text", "write_text"}
    for t in listed:
        assert len(t.description or "") > 80, t.name
        assert t.outputSchema, t.name
        for prop, spec in t.inputSchema.get("properties", {}).items():
            assert spec.get("description"), f"{t.name}.{prop} has no description"


@pytest.mark.parametrize("tool,args,code", [
    ("translate_text", {"target_language": "French", "text": "hello"}, "unsupported_language"),
    ("transcribe_audio", {"path": "missing.wav"}, "file_not_found"),
    ("read_text", {"path": "/etc/hosts"}, "path_not_allowed"),
    ("synthesize_speech", {"language": "hi", "text": "नमस्ते", "voice": "Nobody"}, "unknown_voice"),
    ("synthesize_speech", {"language": "ta", "text": "plain English"}, "invalid_argument"),
    ("write_text", {"content": "x", "name": "evil.sh"}, "invalid_argument"),
    ("ocr_document", {"path": "."}, "invalid_argument"),
    ("translate_text", {"target_language": "hi"}, "invalid_argument"),
])
async def test_bad_inputs_return_structured_errors(ctx, tool, args, code):
    async with connect(ctx) as session:
        res = await session.call_tool(tool, args)
    assert res.isError
    text = res.content[0].text
    body = json.loads(text[text.find("{"):])
    assert body["error"]["code"] == code
    assert body["error"]["message"]


async def test_wrong_types_do_not_crash(ctx):
    async with connect(ctx) as session:
        res = await session.call_tool("read_text", {"path": 42, "limit": "lots"})
        assert res.isError
        res = await session.call_tool("synthesize_speech", {})
        assert res.isError
        assert (await session.list_tools()).tools  # still alive


async def test_resources(ctx):
    async with connect(ctx) as session:
        listed = {str(r.uri) for r in (await session.list_resources()).resources}
        voices = json.loads((await session.read_resource("bodhan://voices")).contents[0].text)
    assert {"bodhan://voices", "bodhan://languages", "bodhan://pricing"} <= listed
    assert "Anitha" in voices["voices"]["ta"]["female"]
