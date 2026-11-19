"""Run the Bodhan MCP server.

    python -m bodhan_mcp                      # stdio (for any MCP client)
    python -m bodhan_mcp --http --port 8765   # streamable HTTP at /mcp

Over HTTP, setting BODHAN_MCP_TOKEN requires `Authorization: Bearer <token>` on every request.
stdout carries the protocol on stdio, so all logging goes to stderr.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sys

from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """Reject HTTP requests that do not carry the expected bearer token."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"authorization", b"")
        if hmac.compare_digest(supplied, self.expected):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "unauthorized", "hint": "send Authorization: Bearer <BODHAN_MCP_TOKEN>"}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"),
            (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m bodhan_mcp", description="Bodhan MCP tool server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default=os.environ.get("BODHAN_LOG_LEVEL", "WARNING"))
    args = parser.parse_args(argv)

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    from .server import build_server

    server = build_server()
    # MCPServer configures logging on construction; ours replaces it so nothing touches stdout.
    _configure_logging(args.log_level)

    if not args.http:
        server.run("stdio")
        return

    import uvicorn

    app: ASGIApp = server.streamable_http_app(host=args.host)
    token = os.environ.get("BODHAN_MCP_TOKEN")
    if token:
        app = BearerTokenMiddleware(app, token)
    elif args.host not in ("127.0.0.1", "localhost", "::1"):
        logging.getLogger(__name__).warning("serving on %s without BODHAN_MCP_TOKEN: anyone can call the tools",
                                            args.host)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
