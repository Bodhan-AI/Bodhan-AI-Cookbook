from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fakes import fake_runtime
from mcp import Client

from bodhan_mcp.__main__ import BearerTokenMiddleware
from bodhan_mcp.errors import parse_tool_error
from bodhan_mcp.runtime import set_runtime
from bodhan_mcp.server import build_server

pytestmark = pytest.mark.asyncio

EXPECTED_TOOLS = {
    "inspect_file", "read_text", "transcribe_audio", "translate_text", "synthesize_speech", "ocr_document",
    "verify_speech", "verify_translation", "make_subtitles", "list_languages_and_voices",
}


@pytest.fixture
def runtime(tmp_path: Path):
    rt, fake = fake_runtime(tmp_path)
    set_runtime(rt)
    yield rt, fake
    set_runtime(None)


async def test_round_trip_schemas_errors_and_resources(runtime, tmp_path: Path) -> None:
    async with Client(build_server()) as client:
        listed = await client.list_tools()
        tools = {t.name: t for t in listed.tools}
        assert set(tools) == EXPECTED_TOOLS
        for tool in tools.values():
            assert len(tool.description or "") > 150, tool.name
            assert tool.output_schema and "cost_inr" in tool.output_schema["properties"]
            for name, prop in tool.input_schema.get("properties", {}).items():
                assert prop.get("description"), f"{tool.name}.{name} has no description"

        bad = await client.call_tool("transcribe_audio", {"path": str(tmp_path / "missing.wav")})
        assert bad.is_error
        err = parse_tool_error(bad.content[0].text)
        assert err and err["code"] == "file_not_found" and err["retryable"] is False

        bad = await client.call_tool("synthesize_speech", {"language": "fr", "text": "bonjour"})
        assert bad.is_error and parse_tool_error(bad.content[0].text)["code"] == "unsupported_language"

        bad = await client.call_tool("translate_text", {"target_language": 5})
        assert bad.is_error  # schema validation failure, reported to the caller, server keeps running

        ok = await client.call_tool("translate_text", {"target_language": "hi-IN", "text": "Good morning."})
        assert not ok.is_error and ok.structured_content["target_language"] == "hi"
        assert ok.structured_content["calls"] == 1

        res = await client.read_resource("bodhan://languages")
        langs = json.loads(res.contents[0].text)["languages"]
        assert any(entry["code"] == "ta" and entry["speak"] for entry in langs)
        prompts = await client.list_prompts()
        assert any(p.name == "plan_media_task" for p in prompts.prompts)


async def test_bearer_token_middleware() -> None:
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    transport = httpx.ASGITransport(app=BearerTokenMiddleware(app, "s3cret"))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.post("/mcp")).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await http.post("/mcp", headers={"Authorization": "Bearer s3cret"})).status_code == 200
