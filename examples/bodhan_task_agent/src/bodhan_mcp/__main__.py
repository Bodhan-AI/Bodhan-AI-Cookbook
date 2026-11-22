"""Self-contained MCP stdio JSON-RPC server (newline-delimited UTF-8).

No non-standard server framework needed; works with any compliant MCP stdio client.
Required protocol methods: initialize, notifications/initialized, ping, tools/list, tools/call.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from bodhan_shared.config import load_dotenv
from bodhan_shared.tools import TOOL_SCHEMAS, ToolHub

SERVER_NAME = "bodhan-indic-tools"
SERVER_VERSION = "0.1.0"
SUPPORTED_PROTOCOL = "2025-06-18"


def handle(request: dict, hub: ToolHub) -> dict | None:
    """Parse one MCP JSON-RPC message, returning None for notifications."""
    identifier = request.get("id")
    method = request.get("method", "")
    params = request.get("params") or {}
    if identifier is None:
        return None
    reply = {"jsonrpc": "2.0", "id": identifier}
    if method == "initialize":
        offered = params.get("protocolVersion", SUPPORTED_PROTOCOL)
        reply["result"] = {
            "protocolVersion": offered if offered in {"2025-06-18", "2025-03-26", "2024-11-05"} else SUPPORTED_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Bodhan-only Indic AI operations. Paths must remain inside BODHAN_WORKSPACE."
        }
    elif method == "ping":
        reply["result"] = {}
    elif method == "tools/list":
        reply["result"] = {"tools": TOOL_SCHEMAS}
    elif method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {})
        result = hub.call(name, args)
        reply["result"] = {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
            "isError": not result["ok"],
            "structuredContent": result,
        }
    else:
        reply["error"] = {"code": -32601, "message": f"Unsupported MCP method: {method}"}
    return reply


def main() -> None:
    load_dotenv()
    workdir = os.environ.get("BODHAN_WORKSPACE", ".")
    # Independent server can use its own workspace and budget ceiling.
    hub = ToolHub(Path(workdir), maximum_cost_inr=float(os.environ.get("BODHAN_MAX_COST_INR", "20")))
    for raw in sys.stdin:
        if not raw.strip():
            continue
        request = {}
        try:
            request = json.loads(raw)
            response = handle(request, hub)
            if response is not None:
                # MCP stdio is a machine-to-machine JSON stream. Escape non-ASCII
                # code points in the outer JSON envelope so this works even when
                # Windows launched Python with a cp1252 console/pipe encoding.
                # MCP clients decode \u escapes back to the original Indic text.
                sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
                sys.stdout.flush()
        except (ValueError, TypeError, KeyError) as exc:
            # Even stderr might have a legacy Windows encoding.
            safe_error = str(exc)[:150].encode("ascii", "backslashreplace").decode("ascii")
            print(f"MCP protocol error: {safe_error}", file=sys.stderr)
            # Errors are deliberately not written to stdout for notifications.
            try:
                identifier = request.get("id")
            except (UnboundLocalError, AttributeError):
                identifier = None
            if identifier is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": identifier,
                                             "error": {"code": -32600, "message": "Invalid request"}}) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
