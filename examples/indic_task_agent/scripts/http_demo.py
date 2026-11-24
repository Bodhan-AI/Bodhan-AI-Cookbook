#!/usr/bin/env python3
"""A live demo of the streamable-HTTP transport and its bearer token.

Starts `python -m bodhan_mcp --transport streamable-http` on a free port, then
connects the way a remote MCP client would:

    no token        -> 401
    wrong token     -> 401
    right token     -> six tools, three resources, two prompts

No Bodhan model is called, so this costs nothing.

    python scripts/http_demo.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "demo-mcp-token-not-real"
WANTED = {"probe_media", "detect_language", "transcribe_audio", "ocr_document", "translate_text", "speak_text"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait(port: int, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


async def _catalogue(url: str, token: str | None) -> dict[str, list[str]]:
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(10.0)) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            tools = sorted(t.name for t in (await client.list_tools()).tools)
            resources = sorted(str(r.uri) for r in (await client.list_resources()).resources)
            prompts = sorted(p.name for p in (await client.list_prompts()).prompts)
            return {"tools": tools, "resources": resources, "prompts": prompts}


def _refused(url: str, token: str | None) -> bool:
    try:
        asyncio.run(_catalogue(url, token))
    except Exception:  # noqa: BLE001 - any protocol/auth failure counts as refused
        return True
    return False


def main() -> int:
    port = _free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    env = {**os.environ, "BODHAN_MCP_TOKEN": TOKEN}
    proc = subprocess.Popen(
        [sys.executable, "-m", "bodhan_mcp", "--transport", "streamable-http", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait(port):
            print("FAIL  the HTTP server did not start")
            return 1
        print(f"streamable HTTP on {url}\n")
        rows: list[tuple[str, bool, str]] = []
        rows.append(("without a token the server refuses", _refused(url, None), "401"))
        rows.append(("a wrong token is refused", _refused(url, "wrong"), "401"))
        try:
            got = asyncio.run(_catalogue(url, TOKEN))
            ok = set(got["tools"]) == WANTED
            rows.append(("the bearer token opens the six tools", ok, ", ".join(got["tools"])))
            rows.append(("three resources advertised", len(got["resources"]) == 3, ", ".join(got["resources"])))
            rows.append(("two prompts advertised", len(got["prompts"]) == 2, ", ".join(got["prompts"])))
        except Exception as exc:  # noqa: BLE001
            rows.append(("the bearer token opens the six tools", False, f"{type(exc).__name__}: {exc}"))
        for name, ok, detail in rows:
            print(f"{'PASS' if ok else 'FAIL'}  {name:<46} {detail}")
        failed = [r for r in rows if not r[1]]
        print(f"\n{len(rows) - len(failed)} passed, {len(failed)} failed")
        return 1 if failed else 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
