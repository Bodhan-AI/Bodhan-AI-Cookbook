"""Entrypoint for ``python -m bodhan_mcp`` (stdio MCP server)."""
from __future__ import annotations

import sys

from src.bodhan_mcp.server import main


def run() -> None:
    """Run the FastMCP server over JSON-RPC 2.0 stdio."""
    main()


if __name__ == "__main__":
    run()
    sys.exit(0)
