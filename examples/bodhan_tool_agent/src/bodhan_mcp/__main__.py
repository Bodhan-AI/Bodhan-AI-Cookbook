"""
CLI entry point for running the Bodhan MCP server standalone:
    python -m bodhan_mcp [--transport stdio|sse] [--port 8000] [--token SECRET]
"""

import argparse
from .server import run_server


def main():
    parser = argparse.ArgumentParser(description="Bodhan MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default) or 'sse' (streamable HTTP transport)."
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host for SSE server (default: 127.0.0.1)."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE server (default: 8000)."
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Optional simple bearer token authentication for SSE transport."
    )
    args = parser.parse_args()
    run_server(transport=args.transport, host=args.host, port=args.port, token=args.token)


if __name__ == "__main__":
    main()
