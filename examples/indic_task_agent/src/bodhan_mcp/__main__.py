"""python -m bodhan_mcp            # stdio (what MCP clients run by default)
python -m bodhan_mcp --transport streamable-http --port 8765   # bearer-authenticated HTTP
"""
from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bodhan_mcp", description="Bodhan Indic models over MCP.")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    from .core.config import load_dotenv

    load_dotenv()
    # stdout is the protocol channel on stdio: every log goes to stderr.
    from .server import mcp

    if args.transport == "stdio":
        mcp.run()
        return 0

    token = os.environ.get("BODHAN_MCP_TOKEN")
    if not token:
        print("BODHAN_MCP_TOKEN must be set for --transport streamable-http", file=sys.stderr)
        return 2
    import uvicorn
    from starlette.middleware.base import BaseHTTPMiddleware

    class Bearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization") != f"Bearer {token}":
                from starlette.responses import JSONResponse

                return JSONResponse({"error": {"code": "AUTH_FAILED", "message": "Missing or wrong bearer token."}}, status_code=401)
            return await call_next(request)

    app = mcp.streamable_http_app()
    app.add_middleware(Bearer)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
