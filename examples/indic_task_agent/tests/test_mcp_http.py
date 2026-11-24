"""Streamable HTTP transport with bearer-token auth, driven by the official MCP client
against a real server process."""

import socket
import subprocess
import sys
import time

import httpx
import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_server(tmp_path):
    port = free_port()
    token_file = tmp_path / "token"
    token_file.write_text("test-token-0123456789abcdef", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "bodhan_mcp", "--transport", "http", "--port", str(port), "--token-file", str(token_file),
                             "--artifacts-dir", str(tmp_path / "art"), "--no-cache"], stderr=subprocess.PIPE, cwd=tmp_path,
                            env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(100):
        try:
            httpx.get(url, timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield url, "test-token-0123456789abcdef"
    proc.terminate()
    proc.wait(timeout=10)


def test_requests_without_the_token_are_rejected(http_server):
    url, _ = http_server
    for headers in ({}, {"Authorization": "Bearer wrong-token"}, {"Authorization": "Basic abc"}):
        r = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers, timeout=5)
        assert r.status_code == 401 and r.json()["error"] == "unauthorized"


async def test_authorised_client_round_trip(http_server):
    url, token = http_server
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with Client(streamable_http_client(url, http_client=http)) as c:
        tools = (await c.list_tools()).tools
        assert {"translate_text", "synthesize_speech", "ocr_document", "transcribe_audio"} <= {t.name for t in tools}
        r = await c.call_tool("detect_language", {"text": "ఇది తెలుగు వాక్యం"})
        assert not r.is_error and r.structured_content["detection"]["language"] == "te"
        bad = await c.call_tool("translate_text", {"text": "hello", "target_language": "fr"})
        assert bad.is_error and bad.structured_content["error"]["code"] == "unsupported_language"
        voices = await c.read_resource("bodhan://voices")
        assert "Ishita" in voices.contents[0].text
        with pytest.raises(MCPError):
            await c.read_resource("bodhan://nope")
