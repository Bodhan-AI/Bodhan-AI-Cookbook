"""CLI Entrypoint for Bodhan MCP Server over stdio."""

import sys
from bodhan_mcp.server import run_stdio_server


def main() -> None:
    """Run the MCP server over stdio."""
    try:
        run_stdio_server()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(f"Bodhan MCP Server error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
