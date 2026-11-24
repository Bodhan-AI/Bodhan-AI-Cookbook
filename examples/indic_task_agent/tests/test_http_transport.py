"""Streamable HTTP transport with bearer-token auth (stretch goal)."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from bodhan_mcp.http import build_app
from bodhan_mcp.server import mcp as server

TOKEN = "test-token-0123456789"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server():
    port = _free_port()
    config = uvicorn.Config(build_app(server, TOKEN), host="127.0.0.1", port=port, log_level="error")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    uv.should_exit = True
    thread.join(timeout=5)


def test_rejects_missing_or_wrong_token(http_server):
    assert httpx.post(http_server, json={}).status_code == 401
    assert httpx.post(http_server, json={}, headers={"Authorization": "Bearer nope"}).status_code == 401


async def test_authorised_client_lists_tools(http_server):
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        async with streamable_http_client(http_server, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
    assert "translate_text" in names and "ocr_document" in names
