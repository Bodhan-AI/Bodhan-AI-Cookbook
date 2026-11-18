"""MCP server (stdio). Tools are registered here; see tools/ for implementations.

NOTE: stdout is the MCP protocol channel. Never print() to stdout in this package;
log to stderr only.
"""

from __future__ import annotations

import sys


def main() -> None:
    print("bodhan_mcp: server not implemented yet (scaffold)", file=sys.stderr)
    raise SystemExit(2)
