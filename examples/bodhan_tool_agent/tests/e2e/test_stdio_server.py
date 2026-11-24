"""Start `python -m bodhan_mcp` as a subprocess and drive it over stdio like a third-party MCP client."""

import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED = {"supported_languages", "list_voices", "inspect_media", "read_text", "make_srt", "ocr_document",
            "transcribe_audio", "translate_text", "translate_segments", "synthesize_speech"}


def payload(result) -> dict:
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if sc:
        return sc.get("result", sc)
    return json.loads(result.content[0].text)


async def test_stdio_server_schemas_and_bad_inputs(tmp_path):
    # BODHAN_MAX_COST_INR=0: this test may read a real .env, but it can never spend money.
    env = {"BODHAN_CACHE_PATH": "", "BODHAN_WORKSPACE": str(tmp_path), "BODHAN_MAX_COST_INR": "0", "BODHAN_NO_DOTENV": "1",
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert "LOCAL FILE PATHS" in (init.instructions or "")

        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert EXPECTED <= set(tools)
        for t in tools.values():
            schema = getattr(t, "input_schema", None) or t.inputSchema
            assert len(t.description or "") > 80, t.name
            for name, prop in schema.get("properties", {}).items():
                assert prop.get("description"), f"{t.name}.{name} has no description"

        # Bad inputs come back as structured errors, and the server keeps serving.
        r = payload(await session.call_tool("inspect_media", {"path": str(tmp_path / "missing.wav")}))
        assert r["ok"] is False and r["error"]["code"] == "FILE_NOT_FOUND" and r["error"]["hint"]
        r = payload(await session.call_tool("translate_text", {"target_language": "French", "text": "Hello"}))
        assert r["error"]["code"] == "UNSUPPORTED_LANGUAGE"
        r = payload(await session.call_tool("synthesize_speech", {"language": "bn", "text": "English words here"}))
        assert r["error"]["code"] == "INVALID_ARGUMENT" and "translate_text" in r["error"]["hint"]
        bad_type = await session.call_tool("ocr_document", {"path": 123})
        assert bad_type.is_error if hasattr(bad_type, "is_error") else bad_type.isError
        missing_arg = await session.call_tool("transcribe_audio", {})
        assert missing_arg.is_error if hasattr(missing_arg, "is_error") else missing_arg.isError

        ok = payload(await session.call_tool("list_voices", {"language": "ta", "gender": "male"}))
        assert ok["ok"] and ok["voices"][0]["voice"] == "Arun"

        resources = {str(r.uri) for r in (await session.list_resources()).resources}
        assert {"bodhan://voices", "bodhan://languages"} <= resources
