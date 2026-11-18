"""The MCP surface through the in-memory 2.x client."""
from __future__ import annotations

import asyncio
import json

from mcp.client import Client
from PIL import Image

from bodhan_mcp.core import catalog
from bodhan_mcp.server import mcp

TOOLS = {"probe_media", "detect_language", "transcribe_audio", "ocr_document", "translate_text", "speak_text"}


def run(fn):
    async def go():
        async with Client(mcp) as c:
            return await fn(c)

    return asyncio.run(go())


def text_of(result) -> str:
    return "\n".join(getattr(part, "text", "") for part in result.content)


def error_of(body: str) -> dict:
    """The framework prefixes 'Error executing tool X: ' to the JSON payload."""
    return json.loads(body[body.index("{"):])["error"]


def test_exactly_six_tools_with_descriptions_and_typed_schemas():
    tools = run(lambda c: c.list_tools()).tools
    assert {t.name for t in tools} == TOOLS and len(tools) == 6
    for t in tools:
        assert t.description and len(t.description) > 40
        schema = getattr(t, "input_schema", None) or t.inputSchema
        assert schema["properties"]
        for name, prop in schema["properties"].items():
            assert "type" in prop or "anyOf" in prop, f"{t.name}.{name} is untyped"
    translate = next(t for t in tools if t.name == "translate_text")
    schema = getattr(translate, "input_schema", None) or translate.inputSchema
    assert set(schema["required"]) == {"source_path", "target_language"}


def test_missing_file_is_a_structured_error_without_a_traceback(tmp_path):
    r = run(lambda c: c.call_tool("transcribe_audio", {"audio_path": str(tmp_path / "nope.wav")}))
    assert r.is_error is True
    body = text_of(r)
    assert "Traceback" not in body
    err = error_of(body)
    assert err["code"] == "MISSING_FILE" and {"message", "retryable", "hint"} <= err.keys()


def test_french_target_is_a_structured_error(bodhan, tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("Hello")
    r = run(lambda c: c.call_tool("translate_text", {"source_path": str(src), "target_language": "fr"}))
    assert r.is_error is True
    body = text_of(r)
    assert "Traceback" not in body
    err = error_of(body)
    assert err["code"] == "UNSUPPORTED_LANGUAGE" and err["hint"] and err["retryable"] is False
    assert bodhan.requests == []


def test_missing_required_argument_is_an_error_not_a_crash():
    r = run(lambda c: c.call_tool("translate_text", {"target_language": "ta"}))
    assert r.is_error is True
    assert "Traceback" not in text_of(r)


def test_resources_are_listed_and_readable():
    async def go(c):
        listed = {str(r.uri) for r in (await c.list_resources()).resources}
        read = {u: (await c.read_resource(u)).contents[0].text for u in sorted(listed)}
        return listed, read

    listed, read = run(go)
    assert listed == {"bodhan://languages", "bodhan://voices", "bodhan://pricing"}
    langs = json.loads(read["bodhan://languages"])["languages"]
    assert {row["code"] for row in langs} == set(catalog.LANGUAGE_NAMES)
    assert json.loads(read["bodhan://pricing"])["prices_inr"]["ocr"] == {"per_image": 0.2}
    voices = json.loads(read["bodhan://voices"])["voices"]
    assert "Anitha" in voices["ta"]["female"]
    for lang in catalog.TTS_LANGUAGES:
        assert voices[lang]["default"] == catalog.resolve_voice(lang, None, None)


def test_tool_results_never_carry_base64(bodhan, tmp_path):
    png = tmp_path / "scan.png"
    Image.new("RGB", (60, 40), "white").save(png)
    out = tmp_path / "o"

    async def go(c):
        ocr = await c.call_tool("ocr_document", {"input_path": str(png), "out_dir": str(out)})
        probe = await c.call_tool("probe_media", {"paths": [str(png)]})
        return ocr, probe

    ocr, probe = run(go)
    assert ocr.is_error is False and probe.is_error is False
    assert len(bodhan.of("ocr")) == 1
    for r in (ocr, probe):
        body = text_of(r) + json.dumps(r.structured_content or {})
        assert "base64" not in body and "data:image" not in body
    assert (out / "scan.ocr.md").is_file()


def test_two_prompts_are_listed_and_render_without_indic_text():
    prompts = run(lambda c: c.list_prompts()).prompts
    assert {p.name for p in prompts} == {"document_to_audio", "audio_to_subtitles"}
    got = run(lambda c: c.get_prompt("document_to_audio", {"input_path": "/x/a.pdf", "target_language": "ta"}))
    body = " ".join(m.content.text for m in got.messages)
    assert "ocr_document" in body and "speak_text" in body
