"""The server as a third-party MCP client sees it: schemas, structured errors, never a crash."""

import json
import sys

from mcp import Client, StdioServerParameters

from bodhan_mcp.server import build_server
from fakes import tone_wav

EXPECTED = {"inspect_media", "transcribe_audio", "translate_text", "synthesize_speech", "ocr_document",
            "make_subtitles", "list_voices"}


def _err(res) -> dict:
    assert res.is_error
    text = res.content[0].text
    return json.loads(text[text.index("{"):])["error"]


async def test_tools_have_descriptions_and_typed_schemas(service):
    async with Client(build_server(service=service)) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == EXPECTED
        for t in tools.values():
            assert len(t.description or "") > 60
            assert t.input_schema["type"] == "object" and t.output_schema
            for name, prop in t.input_schema.get("properties", {}).items():
                assert prop.get("description") or name == "paths" or "anyOf" in prop, (t.name, name)


async def test_bad_inputs_return_structured_actionable_errors(service, tmp_path):
    async with Client(build_server(service=service)) as c:
        e = _err(await c.call_tool("translate_text", {"target_language": "French", "text": "hi"}))
        assert e["code"] == "unsupported_language" and "hint" in e
        e = _err(await c.call_tool("transcribe_audio", {"path": str(tmp_path / "missing.wav")}))
        assert e["code"] == "file_not_found"
        e = _err(await c.call_tool("translate_text", {"target_language": "hi"}))
        assert e["code"] == "invalid_input" and "text_file" in e["message"]
        e = _err(await c.call_tool("synthesize_speech", {"language": "ta", "text": "வணக்கம்", "voice": "Nobody"}))
        assert e["code"] == "unknown_voice" and "Anitha" in e["hint"]
        e = _err(await c.call_tool("ocr_document", {"path": str(tmp_path), "pages": "x"}))
        assert e["code"] in {"invalid_input", "file_not_found"}
        r = await c.call_tool("translate_text", {"target_language": 42})  # schema violation
        assert r.is_error
        # The server is still healthy after all of that.
        ok = await c.call_tool("list_voices", {"language": "Bengali"})
        assert not ok.is_error and ok.structured_content["voices"]["bn"]["female"] == ["Ishita"]


async def test_schema_violations_return_structured_errors(service):
    async with Client(build_server(service=service)) as c:
        # A wrong argument name: names the missing one, the unknown one, and suggests the fix.
        e = _err(await c.call_tool("transcribe_audio", {"audio_path": "/x.wav"}))
        assert e["code"] == "invalid_input" and not e["retryable"]
        assert "path" in e["message"] and "audio_path" in e["message"]
        assert "Did you mean 'path'" in e["hint"] and "path" in e["details"]["required_arguments"]
        # A wrong type and a bad enum value.
        e = _err(await c.call_tool("translate_text", {"target_language": 42}))
        assert e["code"] == "invalid_input" and "target_language" in e["message"]
        e = _err(await c.call_tool("make_subtitles", {"segments_file": "a.json", "format": "ass"}))
        assert e["code"] == "invalid_input" and "format" in e["message"]
        # An unknown tool.
        e = _err(await c.call_tool("dub_video", {}))
        assert e["code"] == "invalid_input" and "transcribe_audio" in e["hint"]


async def test_media_moves_by_path_not_base64(service, tmp_path):
    wav = tmp_path / "clip.wav"
    wav.write_bytes(tone_wav(4.0, sr=16000))
    async with Client(build_server(service=service)) as c:
        r = await c.call_tool("transcribe_audio", {"path": str(wav), "output_dir": str(tmp_path / "o")})
        assert not r.is_error
        payload = json.dumps(r.structured_content)
        assert "base64" not in payload and len(payload) < 20_000
        assert r.structured_content["segments_file"].endswith(".segments.json")


async def test_resources_and_prompt(service):
    async with Client(build_server(service=service)) as c:
        voices = await c.read_resource("bodhan://voices")
        assert "Anitha" in voices.contents[0].text
        langs = json.loads((await c.read_resource("bodhan://languages")).contents[0].text)
        assert "ta" in langs["speak"] and "fr" not in langs["translate"]
        prompts = await c.list_prompts()
        assert any(p.name == "plan_language_task" for p in prompts.prompts)


async def test_runs_standalone_over_stdio(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], cwd=str(tmp_path),
                                   env={"BODHAN_CACHE_DIR": str(tmp_path / "c"), "PATH": "/usr/bin:/bin"})
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == EXPECTED
        e = _err(await c.call_tool("translate_text", {"target_language": "ta", "text": "Hello"}))
        assert e["code"] == "missing_api_key" and "BODHAN_TRANSLATE_KEY" in e["hint"]
