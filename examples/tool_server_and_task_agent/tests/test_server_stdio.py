"""Stdio MCP smoke test: start ``python -m bodhan_mcp`` as a subprocess,
list tools, call one, and read the voices resource — all over real MCP.

Run:  pytest -q
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"


async def _run_stdio_check() -> dict:
    env = dict(os.environ)
    env["BODHAN_MOCK"] = "1"
    env["PYTHONPATH"] = str(ROOT / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(
                "transcribe_audio",
                {"file": str(SAMPLE / "voice_note_hi.wav")},
            )
            resource = await session.read_resource("bodhan://voices")
            return {
                "tool_names": {t.name for t in tools.tools},
                "transcript": result.content[0].text,
                "voices": resource.contents[0].text,
            }


def test_stdio_server_lists_tools_and_answers_a_call():
    out = anyio.run(_run_stdio_check)
    assert out["tool_names"] == {
        "transcribe_audio", "translate_text", "synthesize_speech",
        "ocr_document", "inspect_media", "identify_language",
    }
    assert "सुजल" in out["transcript"]
    assert "Anitha" in out["voices"]
