"""CLI entrypoint for `python -m bodhan_mcp` (stdio default; optional --http --token)."""

from __future__ import annotations

import argparse
import os
import sys

from bodhan_mcp.server import _configure_stderr_logging, build_http_app, build_mcp_server, load_dotenv_silently


def main(argv: list[str] | None = None) -> int:
    _configure_stderr_logging()
    load_dotenv_silently()

    parser = argparse.ArgumentParser(
        prog="python -m bodhan_mcp",
        description="Run the Bodhan MCP tool server over stdio (default) or Streamable HTTP.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve over Streamable HTTP instead of stdio.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765, help="HTTP bind port (default: 8765).")
    parser.add_argument(
        "--token",
        default=os.environ.get("BODHAN_MCP_TOKEN"),
        help="Bearer token for --http mode (or set BODHAN_MCP_TOKEN).",
    )
    parser.add_argument(
        "--work-dir",
        default=os.environ.get("BODHAN_MCP_WORK_DIR"),
        help="Directory for tool-produced intermediate artifacts.",
    )
    args = parser.parse_args(argv)

    from bodhan_mcp.tools import BodhanToolkit

    toolkit = BodhanToolkit(work_dir=args.work_dir)
    mcp = build_mcp_server(toolkit=toolkit)

    if args.http:
        import uvicorn

        app = build_http_app(mcp, token=args.token, host=args.host)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return 0

    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
