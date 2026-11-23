"""CLI entrypoint for running the Bodhan MCP server."""

from __future__ import annotations

import argparse
from bodhan_mcp.server import run_http, run_stdio


def main():
    parser = argparse.ArgumentParser(
        prog="bodhan_mcp",
        description="Bodhan MCP Tool Server (Model Context Protocol)",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run over Streamable HTTP instead of default STDIO transport",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP server host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="HTTP server port (default: 8000)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token for HTTP authentication (defaults to MCP_AUTH_TOKEN env var)",
    )

    args = parser.parse_args()

    if args.http:
        run_http(host=args.host, port=args.port, bearer_token=args.token)
    else:
        run_stdio()


if __name__ == "__main__":
    main()
