"""Run the Bodhan MCP server.

    python -m bodhan_mcp                                   # stdio (default), for any MCP client
    python -m bodhan_mcp --transport http --port 8765      # streamable HTTP, requires BODHAN_MCP_TOKEN
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bodhan_mcp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-auth", action="store_true", help="allow HTTP without a bearer token (local testing only)")
    parser.add_argument("--log-level", default=os.environ.get("BODHAN_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)

    load_dotenv(find_dotenv(usecwd=True))  # .env from the current directory, not the package location
    # stdout is the MCP channel on stdio: all logs go to stderr.
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from .config import Settings
    from .server import build_server, serve_http

    settings = Settings.from_env()
    missing = [m for m in ("indic-transcribe", "indic-translate", "indic-speak", "indic-ocr") if m not in settings.keys]
    if missing:
        logging.getLogger("bodhan_mcp").warning("no API key for: %s (those tools will return missing_api_key)", ", ".join(missing))
    server = build_server(settings=settings)

    if args.transport == "stdio":
        server.run("stdio")
        return 0
    if not settings.http_token and not args.no_auth:
        print("Refusing to serve HTTP without BODHAN_MCP_TOKEN (or pass --no-auth for local testing).", file=sys.stderr)
        return 2
    serve_http(server, args.host, args.port, None if args.no_auth else settings.http_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
