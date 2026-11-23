"""CLI entry point for running the Bodhan MCP server standalone."""

from __future__ import annotations

import argparse
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from bodhan_mcp.server import MCPServer, TOOLS_REGISTRY, RESOURCES_REGISTRY, PROMPTS_REGISTRY


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bodhan Indic AI Models - Model Context Protocol (MCP) Server"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol: 'stdio' (default) or 'sse' (HTTP Server-Sent Events)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for SSE/HTTP transport (default: 8000)",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Bearer token for HTTP/SSE authentication",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print registered MCP tools and JSON schemas, then exit",
    )
    parser.add_argument(
        "--list-resources",
        action="store_true",
        help="Print registered MCP resources (e.g. voice library, pricing), then exit",
    )
    args = parser.parse_args()

    if args.list_tools:
        tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            }
            for t in TOOLS_REGISTRY.values()
        ]
        print(json.dumps(tools, indent=2, ensure_ascii=False))
        return 0

    if args.list_resources:
        resources = [
            {
                "uri": r["uri"],
                "name": r["name"],
                "description": r["description"],
                "mimeType": r["mimeType"],
            }
            for r in RESOURCES_REGISTRY.values()
        ]
        print(json.dumps(resources, indent=2, ensure_ascii=False))
        return 0

    server = MCPServer()
    if args.transport == "sse":
        server.run_sse(port=args.port, auth_token=args.token)
    else:
        server.run_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())
