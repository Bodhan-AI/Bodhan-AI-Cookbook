"""Run the Bodhan MCP server.

    python -m bodhan_mcp                                  # stdio (default), for any MCP client
    python -m bodhan_mcp --transport http --port 8765     # Streamable HTTP at /mcp
                                                          # requires MCP_HTTP_TOKEN (Bearer auth)
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import sys


def _http(app, host: str, port: int, token: str | None, allow_no_auth: bool) -> None:
    import uvicorn
    from starlette.responses import JSONResponse
    from starlette.types import ASGIApp, Receive, Scope, Send

    if not token and not allow_no_auth:
        sys.exit("Set MCP_HTTP_TOKEN to require 'Authorization: Bearer <token>' (or pass --no-auth for local testing).")

    inner = app.streamable_http_app(host=host)

    class BearerAuth:
        def __init__(self, wrapped: ASGIApp) -> None:
            self.wrapped = wrapped

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http" and token:
                headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
                given = headers.get("authorization", "")
                if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
                    resp = JSONResponse({"error": {"code": "unauthorized", "message": "Missing or invalid bearer token."}}, status_code=401)
                    await resp(scope, receive, send)
                    return
            await self.wrapped(scope, receive, send)

    uvicorn.run(BearerAuth(inner), host=host, port=port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m bodhan_mcp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-auth", action="store_true", help="HTTP only: allow requests without a token (local testing).")
    parser.add_argument("--log-level", default=os.environ.get("BODHAN_MCP_LOG_LEVEL", "WARNING"))
    args = parser.parse_args(argv)

    # stdout carries the MCP protocol on stdio, so logs go to stderr.
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .server import create_server

    app = create_server()
    if args.transport == "stdio":
        app.run("stdio")
    else:
        _http(app, args.host, args.port, os.environ.get("MCP_HTTP_TOKEN"), args.no_auth)


if __name__ == "__main__":
    main()
