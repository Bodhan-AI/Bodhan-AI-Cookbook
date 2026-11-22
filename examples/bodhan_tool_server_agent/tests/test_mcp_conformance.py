"""Drive the real server over stdio like a third-party client: schemas present, bad input -> structured errors."""
import json
import os
import sys

from conftest import make_wav
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio_server_schemas_and_structured_errors(env):
    make_wav(env / "a.wav", 1, rate=16000)
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = (await s.list_tools()).tools
        names = {t.name for t in tools}
        assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document", "inspect_media"} <= names
        assert all(t.description and len(t.description) > 30 for t in tools)
        assert all(t.outputSchema and t.annotations is not None for t in tools), "typed outputs and annotations on every tool"
        paid = {t.name: t for t in tools}["transcribe_audio"]
        assert {"language", "text_ref", "segments_ref", "cost_inr", "warnings", "request_ids"} <= set(paid.outputSchema["properties"])
        assert paid.annotations.readOnlyHint is False and paid.annotations.openWorldHint is True
        assert {t.name: t for t in tools}["list_voices"].annotations.readOnlyHint is True

        async def err(name, args):
            res = await s.call_tool(name, args)
            assert res.isError, (name, args)
            return json.loads(res.content[0].text[res.content[0].text.index("{"):])["code"]

        assert await err("inspect_media", {"path": "/etc/passwd"}) == "PATH_NOT_ALLOWED"
        assert await err("inspect_media", {"path": str(env / "missing.wav")}) == "FILE_NOT_FOUND"
        assert await err("translate_text", {"text": "hi", "target_language": "fr"}) == "UNSUPPORTED_LANGUAGE"
        assert await err("translate_text", {"target_language": "ta"}) == "INVALID_ARGUMENT"
        assert await err("synthesize_speech", {"text": "x", "language": "ta", "voice": "Nope"}) == "INVALID_ARGUMENT"
        assert await err("transcribe_audio", {"path": str(env / "a.wav"), "language": "xx"}) == "UNSUPPORTED_LANGUAGE"
        assert await err("ocr_document", {"path": str(env / "a.wav")}) == "UNSUPPORTED_FORMAT"
        bad = await s.call_tool("ocr_document", {"path": 123})  # wrong type: rejected, not crashed
        assert bad.isError
        ok = await s.call_tool("inspect_media", {"path": str(env / "a.wav")})  # server still alive
        assert not ok.isError and json.loads(ok.content[0].text)["kind"] == "audio"
        langs = json.loads((await s.call_tool("list_languages", {"model": "speak"})).content[0].text)
        assert "ta" in langs["speak"] and "fr" not in langs["speak"]
