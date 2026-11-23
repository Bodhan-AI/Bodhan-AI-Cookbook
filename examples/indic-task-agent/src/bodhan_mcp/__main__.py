"""
Main entry point for running the Bodhan MCP Server over stdio.
Usage:
    python -m bodhan_mcp
"""

from bodhan_mcp.server import run_stdio_server

if __name__ == "__main__":
    run_stdio_server()
