"""The standalone server over streamable HTTP: bearer required, six tools when it is right."""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-mcp-token-not-real"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(port: int, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


@pytest.fixture
def server():
    port = _free_port()
    env = {**os.environ, "BODHAN_MCP_TOKEN": TOKEN}
    proc = subprocess.Popen(
        [sys.executable, "-m", "bodhan_mcp", "--transport", "streamable-http", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait(port), "the HTTP server did not start"
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _tools(url: str, headers: dict[str, str] | None = None) -> set[str]:
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async def go() -> set[str]:
        async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(10.0)) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                return {t.name for t in (await client.list_tools()).tools}

    return asyncio.run(go())


def test_bearer_token_is_required(server):
    with pytest.raises(Exception):
        _tools(server)
    with pytest.raises(Exception):
        _tools(server, {"Authorization": "Bearer wrong"})


def test_the_token_opens_the_six_tools(server):
    tools = _tools(server, {"Authorization": f"Bearer {TOKEN}"})
    assert tools == {"probe_media", "detect_language", "transcribe_audio", "ocr_document", "translate_text", "speak_text"}
