"""Streamable-HTTP transport: the same tools, but only for callers presenting the bearer token."""
import json

import pytest
from starlette.testclient import TestClient

from bodhan_mcp.server import build_http_app

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_refuses_to_start_without_a_token():
    with pytest.raises(SystemExit):
        build_http_app("")


def test_requests_without_or_with_wrong_token_are_rejected():
    with TestClient(build_http_app("s3cret"), base_url="http://localhost:8000") as c:
        assert c.post("/mcp", json=INIT, headers=HEADERS).status_code == 401
        assert c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer nope"}).status_code == 401
        r = c.post("/mcp", json=INIT, headers=HEADERS)
        assert r.json()["error"]["code"] == "unauthorized" and r.headers["www-authenticate"] == "Bearer"


def test_valid_token_reaches_the_mcp_endpoint():
    with TestClient(build_http_app("s3cret"), base_url="http://localhost:8000") as c:
        r = c.post("/mcp", json=INIT, headers={**HEADERS, "Authorization": "Bearer s3cret"})
        assert r.status_code == 200
        body = r.text
        data = json.loads(body.split("data:", 1)[1].strip()) if "data:" in body else r.json()
        assert data["result"]["serverInfo"]["name"] == "bodhan"
