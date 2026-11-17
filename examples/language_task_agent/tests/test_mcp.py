"""Real stdio protocol check using the off-the-shelf official Python MCP client."""

import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest


@pytest.mark.asyncio
async def test_off_the_shelf_mcp_client(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    (inputs / "example.txt").write_text("Synthetic English source.")
    env = {
        **os.environ,
        "BODHAN_INPUT_ROOT": str(inputs),
        "BODHAN_OUTPUT_ROOT": str(tmp_path / "output"),
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {"ocr", "transcribe", "translate", "synthesize", "write_translation_index"} <= names
            result = await client.call_tool("list_files", {})
            body = json.loads(result.content[0].text)
            assert body["ok"] and body["files"]
            ref = body["files"][0]["ref"]
            bad = await client.call_tool("translate", {"source": ref, "target_language": "fr"})
            assert json.loads(bad.content[0].text)["error"]["code"] == "unsupported_language"
            unverified = await client.call_tool("write_translation_index", {"translations": [ref]})
            assert json.loads(unverified.content[0].text)["error"]["code"] == "unverified_translation"
            missing = await client.call_tool("read_text", {"source": "../../etc/passwd"})
            assert json.loads(missing.content[0].text)["error"]["code"] == "missing_file"
            # Server remains alive after invalid input.
            again = await client.call_tool("read_text", {"source": ref})
            assert json.loads(again.content[0].text)["text"] == "Synthetic English source."


@pytest.mark.asyncio
async def test_mcp_long_session_and_restart_keep_refs(tmp_path, monkeypatch):
    from bodhan_mcp import __main__ as module

    monkeypatch.setenv("BODHAN_INPUT_ROOT", str(tmp_path / "inputs"))
    monkeypatch.setenv("BODHAN_OUTPUT_ROOT", str(tmp_path / "outputs"))
    monkeypatch.setattr(module, "_service", None)
    first = await module.safe_call("write_english", {"text": "Café summary."})
    assert first["ok"]
    old_output = module.service().run.output
    for _ in range(40):
        result = await module.safe_call("read_text", {"source": first["ref"]})
        assert result["ok"] and result["text"] == "Café summary."
    module._service = None
    second = await module.safe_call("list_files", {})
    assert second["ok"] and module.service().run.output != old_output
