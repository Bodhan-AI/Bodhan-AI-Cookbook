"""Streamable HTTP + bearer token, and MCP resources/prompts, driven by the official MCP SDK client."""

import asyncio
import json
import socket
import threading

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from bodhan_mcp import server

TOKEN = "test-token-123"


@pytest.fixture(scope="module")  # FastMCP builds its session manager once per process
def http_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server.mcp.settings.host, server.mcp.settings.port = "127.0.0.1", port
    srv = uvicorn.Server(uvicorn.Config(server.build_http_app(TOKEN), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=lambda: asyncio.run(srv.serve()), daemon=True)
    t.start()
    while not srv.started:
        t.join(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    srv.should_exit = True
    t.join(5)


async def test_http_rejects_missing_or_wrong_token(http_server):
    async with httpx.AsyncClient() as c:
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}):
            r = await c.post(http_server, headers=headers, json={})
            assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


async def test_http_with_token_lists_tools_and_reads_resources(http_server):
    async with streamablehttp_client(http_server, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _), ClientSession(r, w) as session:
        await session.initialize()
        assert "translate_text" in {t.name for t in (await session.list_tools()).tools}
        voices = json.loads((await session.read_resource("bodhan://voices")).contents[0].text)
        assert voices["ta"] == {"female": ["Anitha"], "male": ["Arun"]}
        one = json.loads((await session.read_resource("bodhan://voices/hi")).contents[0].text)
        assert one["female"] == ["Kavya", "Suhani"]
        langs = json.loads((await session.read_resource("bodhan://languages")).contents[0].text)
        assert "fr" not in langs["translate"] and "ta" in langs["speak"]
        prompts = {p.name for p in (await session.list_prompts()).prompts}
        assert {"translate_document", "speak_in_language", "subtitle_audio"} <= prompts
        got = await session.get_prompt("subtitle_audio", {"audio_path": "a.wav", "target_language": "en"})
        assert "make_subtitles" in got.messages[0].content.text


def test_http_refuses_to_start_without_a_token(monkeypatch):
    monkeypatch.delenv("BODHAN_MCP_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="BODHAN_MCP_TOKEN"):
        server.main(["--transport", "http"])
