#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path


class MCPBridge:
    """Launches bodhan_mcp over stdio via subprocess and bridges tool calls."""

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._tools: list[dict] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
        self._running = False

    async def start(self):
        """Start the MCP server process and session."""
        cmd = [sys.executable, "-m", "bodhan_mcp"]
        env = {**os.environ}
        # Ensure the subprocess can import bodhan_mcp (which lives under src/).
        src_dir = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        self._running = True
        asyncio.get_event_loop().create_task(self._read_loop())
        await self._discover_tools()

    async def stop(self):
        """Stop the MCP server."""
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(
                    self._proc.wait(), timeout=5.0
                )
            except asyncio.TimeoutError:
                self._proc.kill()
        self._running = False

    async def _read_loop(self):
        """Read JSON-RPC messages from stdout."""
        assert self._proc
        buffer = b""
        while self._running and self._proc.stdout \
                and not self._proc.stdout.at_eof():
            chunk = await self._proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    pass

    async def _handle_message(self, msg: dict):
        """Handle incoming JSON-RPC message."""
        request_id = msg.get("id")
        if request_id in self._pending:
            fut = self._pending.pop(request_id)
            if not fut.done():
                fut.set_result(msg)

    async def _request(self, method: str, params=None):
        """Send a JSON-RPC request and wait for response."""
        assert self._proc and self._proc.stdin
        async with self._lock:
            req_id = str(uuid.uuid4())
            loop = asyncio.get_event_loop()
            fut: asyncio.Future = loop.create_future()
            self._pending[req_id] = fut

            message = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                message["params"] = params
            payload = json.dumps(message) + "\n"

            self._proc.stdin.write(payload.encode("utf-8"))
            await self._proc.stdin.drain()

            try:
                return await asyncio.wait_for(fut, timeout=120.0)
            except asyncio.TimeoutError:
                self._pending.pop(req_id, None)
                raise

    async def _discover_tools(self):
        """Discover available tools via initialize + list_tools."""
        await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "bodhan-agent", "version": "0.1.0"},
        })
        resp = await self._request("tools/list")
        for t in resp.get("result", {}).get("tools", []):
            input_schema = t.get("inputSchema", {})
            props = input_schema.get("properties", {})
            required = input_schema.get("required", [])
            params = []
            for name, info in props.items():
                if isinstance(info, dict):
                    params.append({
                        "name": name,
                        "type": info.get("type", "string"),
                        "description": info.get("description", ""),
                        "required": name in required,
                    })
                else:
                    params.append({"name": name, "type": "string"})
            props_dict = {}
            for p in params:
                props_dict[p["name"]] = {
                    "type": p["type"],
                    "description": p["description"],
                }
            self._tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": props_dict,
                        "required": [
                            p["name"] for p in params if p["required"]
                        ],
                    },
                },
            })

    async def list_tools(self) -> list[dict]:
        """Return discovered tool definitions."""
        return self._tools

    async def call_tool(self, name: str, arguments):
        """Call an MCP tool by name with given arguments."""
        try:
            resp = await self._request(
                "tools/call",
                {"name": name, "arguments": arguments},
            )
            content = resp.get("result", {}).get("content", [])
            output_parts = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        output_parts.append(item["text"])
                    elif "data" in item:
                        output_parts.append(str(item["data"]))
                    else:
                        output_parts.append(json.dumps(item))
                else:
                    output_parts.append(str(item))
            result_text = "\n".join(output_parts)

            if resp.get("result", {}).get("isError"):
                return {"error": "TOOL_ERROR", "message": result_text}
            return {"result": result_text}
        except Exception as e:
            return {"error": "TOOL_ERROR", "message": str(e)}


async def main():
    bridge = MCPBridge()
    await bridge.start()
    try:
        tools = await bridge.list_tools()
        print(f"Discovered {len(tools)} tools:")
        for t in tools:
            print(f"  - {t['function']['name']}: "
                  f"{t['function']['description']}")
    finally:
        await bridge.stop()


if __name__ == "__main__":
    asyncio.run(main())
