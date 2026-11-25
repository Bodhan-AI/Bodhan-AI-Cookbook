"""Bearer-token check for the streamable-HTTP transport (plain ASGI middleware, no extra dependency)."""

from __future__ import annotations

import hmac
import json


class BearerTokenMiddleware:
    """Reject every HTTP request that lacks `Authorization: Bearer <token>` with 401 before it reaches MCP."""

    def __init__(self, app, token: str):
        if not token:
            raise ValueError("BearerTokenMiddleware needs a non-empty token")
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":  # lifespan events must pass through
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        supplied = headers.get(b"authorization", b"")
        scheme, _, value = supplied.partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(value.strip(), self._token):  # constant-time compare
            return await self.app(scope, receive, send)
        body = json.dumps({"error": "unauthorized", "message": "Send the header `Authorization: Bearer <BODHAN_MCP_TOKEN>`."}).encode()
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
