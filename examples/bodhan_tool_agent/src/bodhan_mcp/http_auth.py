"""Streamable HTTP transport with a simple bearer-token check (stretch goal)."""

from __future__ import annotations

import hmac
import os
import sys


def run_http(mcp, host: str, port: int) -> None:
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    token = os.environ.get("BODHAN_MCP_TOKEN")
    if not token:
        sys.exit("Set BODHAN_MCP_TOKEN before serving over HTTP.")

    class TokenAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(got, token):
                return JSONResponse({"error": {"code": "unauthorized", "message": "Missing or bad bearer token."}}, 401)
            return await call_next(request)

    app = mcp.streamable_http_app()
    app.add_middleware(TokenAuth)
    uvicorn.run(app, host=host, port=port, log_level="warning")
