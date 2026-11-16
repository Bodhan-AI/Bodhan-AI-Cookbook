"""The server as a third-party MCP client sees it: schemas, structured errors, resources, stdio."""

import json
import os
import sys
from pathlib import Path

import httpx
from mcp import Client, StdioServerParameters

from bodhan_mcp.errors import parse_error_text
from bodhan_mcp.server import create_server

EXPECTED_TOOLS = {"inspect_files", "read_text", "list_voices", "detect_language", "make_subtitles", "transcribe_audio",
                  "translate_text", "synthesize_speech", "verify_speech", "ocr_document"}


def _err(result) -> dict:
    assert result.is_error, result
    err = parse_error_text(result.content[0].text)
    assert err is not None, result.content[0].text
    return err


async def test_tools_are_described_and_typed(settings, fake):
    app = create_server(settings, httpx.MockTransport(fake.handler))
    async with Client(app) as c:
        listed = (await c.list_tools()).tools
        assert {t.name for t in listed} == EXPECTED_TOOLS
        for t in listed:
            assert len(t.description) > 80, t.name
            assert t.input_schema["type"] == "object"
            assert t.output_schema and t.output_schema.get("properties"), t.name
            for name, prop in t.input_schema["properties"].items():
                assert "description" in prop or "enum" in prop or "$ref" in prop or "anyOf" in prop, (t.name, name)
        assert "base64" not in json.dumps([t.input_schema for t in listed]).lower()


async def test_bad_inputs_return_structured_errors(settings, fake, tmp_path):
    app = create_server(settings, httpx.MockTransport(fake.handler))
    async with Client(app) as c:
        e = _err(await c.call_tool("transcribe_audio", {"path": str(tmp_path / "missing.wav")}))
        assert e["code"] == "file_not_found" and e["hint"]
        e = _err(await c.call_tool("translate_text", {"target_language": "fr", "text": "hello"}))
        assert e["code"] == "unsupported_language" and "en" in e["details"]["supported"]
        e = _err(await c.call_tool("synthesize_speech", {"language": "hi"}))
        assert e["code"] == "invalid_input"
        r = await c.call_tool("synthesize_speech", {"language": "hi", "text": "x", "output_format": "aiff"})
        assert r.is_error and "output_format" in r.content[0].text  # schema validation names the field
        r = await c.call_tool("transcribe_audio", {})
        assert r.is_error and "path" in r.content[0].text
        r = await c.call_tool("no_such_tool", {})
        assert r.is_error
        # the server survives all of the above
        ok = await c.call_tool("list_voices", {"language": "bn"})
        assert not ok.is_error and ok.structured_content["default_female"] == "Ishita"


async def test_end_to_end_over_mcp(settings, fake, tmp_path):
    app = create_server(settings, httpx.MockTransport(fake.handler))
    md = tmp_path / "sheet.md"
    md.write_text("| Item | Qty |\n|---|---|\n| Apples | 3 |\n", encoding="utf-8")
    async with Client(app) as c:
        tr = await c.call_tool("translate_text", {"target_language": "mr", "input_path": str(md)})
        assert not tr.is_error, tr.content[0].text
        out = tr.structured_content["output_path"]
        sp = await c.call_tool("synthesize_speech", {"language": "mr", "input_path": out, "output_format": "wav"})
        assert not sp.is_error, sp.content[0].text
        assert sp.structured_content["verification"]["passed"] and Path(sp.structured_content["audio_path"]).exists()
        usage = json.loads((await c.read_resource("bodhan://usage")).contents[0].text)
        assert usage["total_cost_inr"] > 0
        voices = json.loads((await c.read_resource("bodhan://voices")).contents[0].text)
        assert "ta" in voices["voices_by_recorded_language"]
        prompts = (await c.list_prompts()).prompts
        assert any(p.name == "indic_task" for p in prompts)


async def test_stdio_server_runs_standalone(tmp_path):
    env = {**os.environ, "BODHAN_CACHE_DIR": str(tmp_path / "c"), "BODHAN_MCP_OUTPUT_DIR": str(tmp_path / "o"),
           "BODHAN_API_KEY": "", "BODHAN_TRANSLATE_KEY": "", "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env, cwd=str(tmp_path))
    txt = tmp_path / "note.txt"
    txt.write_text("নমস্কার, আজ ক্লাস দশটায়।", encoding="utf-8")
    async with Client(params, read_timeout_seconds=60) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == EXPECTED_TOOLS
        info = await c.call_tool("inspect_files", {"paths": [str(txt)]})
        assert info.structured_content["files"][0]["language"]["language"] == "bn"
        e = _err(await c.call_tool("translate_text", {"target_language": "en", "input_path": str(txt)}))
        assert e["code"] == "missing_api_key" and e["details"]["env_var"] == "BODHAN_TRANSLATE_KEY"
