from __future__ import annotations

import argparse
from pathlib import Path

from bodhan_mcp.server import BodhanMCPServer
from bodhan_mcp.tools import BodhanToolSuite


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bodhan_mcp",
        description="Bodhan AI Model Context Protocol (MCP) Server over stdio or Streamable HTTP.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default="stdio",
        help="MCP transport mode: 'stdio' (default) or 'http'/'streamable-http'.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address when running with --transport http (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port when running with --transport http (default: 8000).",
    )
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Optional Bearer token required for HTTP transport requests.",
    )
    parser.add_argument(
        "--artifacts-dir",
        default="./out/artifacts",
        help="Directory where tool output files are written by default.",
    )
    args = parser.parse_args()

    suite = BodhanToolSuite(artifacts_dir=Path(args.artifacts_dir))
    server = BodhanMCPServer(tool_suite=suite)

    if args.transport in ("http", "streamable-http"):
        server.run_http(host=args.host, port=args.port, auth_token=args.auth_token)
    else:
        server.run_stdio()


if __name__ == "__main__":
    main()
