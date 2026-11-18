"""Run the Bodhan MCP server over stdio: python -m bodhan_mcp"""

from __future__ import annotations

from .server import mcp


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
