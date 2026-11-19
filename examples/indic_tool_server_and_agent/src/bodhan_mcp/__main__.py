"""Entry point for `python -m bodhan_mcp`."""
import sys
import logging

# CRITICAL: log to stderr only — stdout carries the MCP stdio protocol
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from bodhan_mcp.server import mcp  # noqa: E402


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
