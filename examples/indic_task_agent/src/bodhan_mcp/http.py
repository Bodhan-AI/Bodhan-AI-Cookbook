"""Optional streamable-HTTP transport with a static bearer token (stretch goal).

    BODHAN_MCP_TOKEN=some-long-random-string python -m bodhan_mcp --http --port 8765

Clients connect to http://host:8765/mcp with `Authorization: Bearer <token>`. The server refuses to
start without a token so it is never exposed unauthenticated by accident. File paths are resolved on
the server's machine, so HTTP clients must reference files the server can read.
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from typing import Any


class BearerTokenMiddleware:
    """Pure ASGI middleware: rejects HTTP requests without the right bearer token; passes lifespan through."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(auth, self.expected):
                body = json.dumps(
                    {"error": {"code": "unauthorized", "message": "Missing or invalid bearer token."}}
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def build_app(server: Any, token: str, host: str = "127.0.0.1") -> Any:
    return BearerTokenMiddleware(server.streamable_http_app(host=host), token)


def serve_http(server: Any, host: str, port: int) -> None:
    import uvicorn

    token = (os.environ.get("BODHAN_MCP_TOKEN") or "").strip()
    if len(token) < 16:
        print("error: set BODHAN_MCP_TOKEN (16+ characters) to serve over HTTP.", file=sys.stderr)
        raise SystemExit(2)
    uvicorn.run(build_app(server, token, host), host=host, port=port, log_level="warning")
