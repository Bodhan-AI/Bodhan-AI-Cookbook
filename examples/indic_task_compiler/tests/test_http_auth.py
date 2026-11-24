"""Unit test for optional Streamable HTTP Bearer token authentication middleware."""

from __future__ import annotations

import httpx
from bodhan_mcp.server import build_http_app, build_mcp_server


async def test_http_app_rejects_missing_or_wrong_bearer_token() -> None:
    mcp = build_mcp_server()
    app = build_http_app(mcp, token="super-secret-test-token")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        r_no_auth = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r_no_auth.status_code == 401

        r_wrong = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer wrong-token"},
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert r_wrong.status_code == 401
