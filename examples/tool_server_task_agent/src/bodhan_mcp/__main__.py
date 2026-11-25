"""`python -m bodhan_mcp` - run the MCP server over stdio (default). Never print to stdout here: it carries the protocol.

Optional: `--transport streamable-http` serves MCP over HTTP; it REQUIRES a bearer token in BODHAN_MCP_TOKEN.
"""
from __future__ import annotations

import argparse
import hmac
import os
import sys


def build_http_app(token: str):
    """The streamable-HTTP ASGI app wrapped in simple bearer-token authentication."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    from .server import mcp

    app = mcp.streamable_http_app()

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                return JSONResponse({"error": "unauthorized", "hint": "Send 'Authorization: Bearer <BODHAN_MCP_TOKEN>'."}, status_code=401)
            return await call_next(request)

    app.add_middleware(BearerAuth)
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bodhan_mcp", description="Bodhan MCP tool server (stdio by default).")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    from . import config

    config.load_env()
    if args.transport == "streamable-http":
        token = os.environ.get("BODHAN_MCP_TOKEN", "").strip()
        if len(token) < 16:
            sys.exit("Refusing to serve over HTTP without authentication: set BODHAN_MCP_TOKEN to a random string of at least 16 characters.")
        import uvicorn

        print(f"[bodhan_mcp] streamable-http on http://{args.host}:{args.port}/mcp (bearer token required)", file=sys.stderr)
        uvicorn.run(build_http_app(token), host=args.host, port=args.port, log_level="warning")
        return
    from .server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
