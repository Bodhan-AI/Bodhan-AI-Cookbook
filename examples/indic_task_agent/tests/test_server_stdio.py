"""The server on its own over stdio, driven like a third-party MCP client would: schemas, bad inputs,
structured errors, resources. The server must never crash on bad input."""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.fakes.fake_bodhan import tone_wav


@asynccontextmanager
async def open_session(fake, tmp_path):
    """Opened inside each test (not a fixture) so anyio's cancel scopes enter and exit in the same task."""
    env = {
        **os.environ,
        "BODHAN_BASE_URL": fake.url,
        "BODHAN_API_KEY": "test-key",
        "BODHAN_CACHE_DIR": str(tmp_path / "c"),
        "BODHAN_MCP_OUTPUT_DIR": str(tmp_path / "o"),
        "BODHAN_MAX_RETRIES": "0",
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env, cwd=str(tmp_path))
    async with stdio_client(params) as streams, ClientSession(streams[0], streams[1]) as s:
        await s.initialize()
        yield s


def _err(res) -> dict:
    assert res.is_error
    text = res.content[0].text
    return json.loads(text[text.find("{") :])["error"]


async def test_tools_are_described_and_typed(fake, tmp_path):
    async with open_session(fake, tmp_path) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert set(tools) == {
            "inspect_files",
            "list_capabilities",
            "transcribe_audio",
            "translate_text",
            "synthesize_speech",
            "ocr_document",
            "make_subtitles",
            "read_text",
        }
        for t in tools.values():
            assert len(t.description) > 80
            for name, prop in t.input_schema.get("properties", {}).items():
                assert prop.get("description"), f"{t.name}.{name} has no description"
        for name in ("transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"):
            assert tools[name].output_schema and "usage" in tools[name].output_schema["properties"]
            assert "max_cost_inr" in tools[name].input_schema["properties"]


@pytest.mark.parametrize(
    "tool,args,code",
    [
        ("translate_text", {"target_language": "fr", "text": "hello"}, "unsupported_language"),
        ("translate_text", {"target_language": "hi"}, "invalid_input"),
        ("translate_text", {"target_language": "hi", "text": "a", "input_path": "/x"}, "invalid_input"),
        ("transcribe_audio", {"path": "/definitely/missing.wav"}, "file_not_found"),
        ("synthesize_speech", {"language": "ta", "text": "English words only."}, "script_mismatch"),
        ("synthesize_speech", {"language": "xx", "text": "hi"}, "unsupported_language"),
        ("ocr_document", {"path": "/nope.pdf"}, "file_not_found"),
        ("make_subtitles", {"segments_path": "/nope.json"}, "file_not_found"),
    ],
)
async def test_bad_inputs_return_structured_errors(fake, tmp_path, tool, args, code):
    async with open_session(fake, tmp_path) as session:
        res = await session.call_tool(tool, args)
        err = _err(res)
        assert err["code"] == code and err["message"]


async def test_schema_violations_do_not_crash_server(fake, tmp_path):
    async with open_session(fake, tmp_path) as session:
        res = await session.call_tool("read_text", {"path": 42, "max_chars": -5})
        assert res.is_error
        res = await session.call_tool("ocr_document", {})
        assert res.is_error
        res = await session.call_tool("list_capabilities", {})  # still alive
        assert not res.is_error


async def test_happy_path_and_resources(fake, tmp_path):
    async with open_session(fake, tmp_path) as session:
        clip = tmp_path / "x.wav"
        tone_wav(clip, 6, 330)
        res = await session.call_tool("transcribe_audio", {"path": str(clip)})
        assert not res.is_error
        data = res.structured_content
        assert data["likely_languages"] == ["te"] and data["usage"]["cost_inr"] > 0
        assert os.path.exists(data["transcript_path"])
        uris = {str(r.uri) for r in (await session.list_resources()).resources}
        assert {"bodhan://catalog", "bodhan://voices", "bodhan://errors"} <= uris
        voices = await session.read_resource("bodhan://voices")
        assert "Anitha" in voices.contents[0].text


async def test_budget_cap_is_enforced_by_server(fake, tmp_path):
    async with open_session(fake, tmp_path) as session:
        clip = tmp_path / "x.wav"
        tone_wav(clip, 60, 220)
        res = await session.call_tool("transcribe_audio", {"path": str(clip), "max_cost_inr": 0.01})
        assert _err(res)["code"] == "budget_exceeded"
