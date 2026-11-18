#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

_TOOL_NAME_MAP: dict[str, str] = {
    "inspectMedia": "inspect_media",
    "transcribeAudio": "transcribe_audio",
    "extractDocumentText": "extract_document_text",
    "translateText": "translate_text",
    "synthesizeSpeech": "synthesize_speech",
}

_XML_TOOL_RE = re.compile(
    r'<\s*function\s*=\s*(\w+)\s*>\s*(.*?)\s*</\s*function\s*>',
    re.DOTALL | re.IGNORECASE,
)

_PARAM_RE = re.compile(
    r'<\s*parameter\s*=\s*([\w_]+)\s*>\s*(.*?)\s*</\s*parameter\s*>',
    re.DOTALL | re.IGNORECASE,
)

VALID_TOOLS = set(_TOOL_NAME_MAP.values())


def normalize_tool_name(name: str) -> str:
    return _TOOL_NAME_MAP.get(name, name)


def parse_xml_tool_call(xml_text: str) -> dict | None:
    match = _XML_TOOL_RE.search(xml_text)
    if not match:
        return None
    raw_name = match.group(1)
    body = match.group(2)
    name = _TOOL_NAME_MAP.get(raw_name, raw_name)
    args: dict = {}
    for pm in _PARAM_RE.finditer(body):
        args[pm.group(1)] = pm.group(2).strip()
    return {"name": name, "arguments": args}


class MCPBridge:
    def __init__(self):
        self._proc = None
        self._tools = []
        self._pending = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._read_task = None

    async def start(self):
        cmd = [sys.executable, "-m", "bodhan_mcp"]
        env = {**os.environ}
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
        self._read_task = asyncio.get_event_loop().create_task(self._read_loop())
        await self._discover_tools()

    async def stop(self):
        self._running = False
        if self._read_task:
            self._read_task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()

    async def _read_loop(self):
        assert self._proc
        buffer = b""
        while self._running and self._proc.stdout and not self._proc.stdout.at_eof():
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
        request_id = msg.get("id")
        if request_id in self._pending:
            fut = self._pending.pop(request_id)
            if not fut.done():
                fut.set_result(msg)

    async def _request(self, method: str, params=None):
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
                        "required": [p["name"] for p in params if p["required"]],
                    },
                },
            })

    async def list_tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, name: str, arguments):
        name = normalize_tool_name(name)
        if name not in VALID_TOOLS:
            valid = ", ".join(sorted(VALID_TOOLS))
            return {"error": "TOOL_ERROR",
                    "message": f"Invalid tool: {name}. Valid: {valid}"}
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
