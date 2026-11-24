"""Drive `python -m bodhan_mcp` over stdio with the official MCP client, like a third-party client would."""
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.asyncio


async def _session(stack, env=None, cwd=None):
    # cwd is an empty temp folder so the server never picks up the developer's real .env
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env, cwd=str(cwd) if cwd else None)
    r, w = await stack.enter_async_context(stdio_client(params))
    s = await stack.enter_async_context(ClientSession(r, w))
    await s.initialize()
    return s


async def test_stdio_server_lists_typed_tools_and_handles_bad_input(tmp_path):
    import contextlib
    async with contextlib.AsyncExitStack() as stack:
        s = await _session(stack, cwd=tmp_path)
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document", "verify_speech",
                "inspect_file", "list_capabilities"} <= set(tools)
        for t in tools.values():
            assert t.description and t.inputSchema["properties"] is not None
            assert t.outputSchema, f"{t.name} has no output schema"
        assert "audio_path" in tools["transcribe_audio"].inputSchema["required"]

        # bad inputs -> isError with a structured, actionable JSON body, never a crash
        for name, args, code in [
            ("inspect_file", {"path": str(tmp_path / "nope")}, "file_not_found"),
            ("translate_text", {"target_language": "French", "text": "hi"}, "unsupported_language"),
            ("translate_text", {"target_language": "ta"}, "invalid_arguments"),
            ("synthesize_speech", {"language": "ta", "text": "hi", "voice": "Nobody"}, "unknown_voice"),
            ("ocr_document", {"input_path": str(tmp_path / "x.pdf")}, "file_not_found"),
        ]:
            r = await s.call_tool(name, args)
            assert r.isError, name
            body = json.loads(r.content[0].text[r.content[0].text.index("{"):])
            assert body["code"] == code and body["message"], (name, body)
        # wrong argument types are rejected by the schema, and the server keeps working afterwards
        r = await s.call_tool("transcribe_audio", {"audio_path": 123, "start_s": "soon"})
        assert r.isError
        r = await s.call_tool("detect_text_language", {"text": "இது ஒரு சோதனை"})
        assert not r.isError and r.structuredContent["language"] == "ta"
        r = await s.call_tool("list_capabilities", {})
        assert "fr" not in r.structuredContent["translate_languages"]
        res = await s.read_resource("bodhan://voices")
        assert "Anitha" in res.contents[0].text


async def test_missing_key_is_reported_not_crashed(tmp_path):
    import contextlib
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("BODHAN_")}
    async with contextlib.AsyncExitStack() as stack:
        s = await _session(stack, env, cwd=tmp_path)
        r = await s.call_tool("translate_text", {"target_language": "ta", "text": "hello"})
        assert r.isError and "missing_api_key" in r.content[0].text and "BODHAN_TRANSLATE_KEY" in r.content[0].text
