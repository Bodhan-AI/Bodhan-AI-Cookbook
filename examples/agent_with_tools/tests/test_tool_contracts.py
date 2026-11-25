"""Every MCP tool has typed input/output, a description, and actionable errors; media goes by file path."""

import pytest

from bodhan_mcp import server
from bodhan_mcp.tools import ocr as ocr_mod, speak as speak_mod, transcribe as transcribe_mod


@pytest.mark.asyncio
async def test_every_tool_is_typed_and_described():
    tools = await server.mcp.list_tools()
    assert {t.name for t in tools} == {"translate_text", "transcribe_audio", "speak_text", "ocr_document", "make_subtitles", "inspect_media", "detect_language", "read_text_file", "write_text_file"}
    for t in tools:
        assert len(t.description or "") > 80, t.name
        assert t.outputSchema, f"{t.name} has no output schema"
        for pname, prop in t.inputSchema["properties"].items():
            assert prop.get("description"), f"{t.name}.{pname} has no description"
        assert "error" in t.outputSchema["properties"], t.name


@pytest.mark.asyncio
async def test_no_tool_takes_inline_media():
    for t in await server.mcp.list_tools():
        for pname in t.inputSchema["properties"]:
            assert "base64" not in pname and "bytes" not in pname, f"{t.name}.{pname}"


def test_detect_language_no_letters_is_a_typed_error():
    r = server.detect_language("1234 !!")
    assert not r.ok and r.error.code == "no_letters" and "Pass text" in r.error.message
    assert server.detect_language("வணக்கம்").language == "ta"


@pytest.mark.asyncio
async def test_missing_and_directory_inputs_are_actionable(tmp_path):
    r = await transcribe_mod.transcribe(str(tmp_path / "nope.wav"))
    assert r.error.code == "file_not_found" and "audio_path" in r.error.message and "existing" in r.error.message
    r = await ocr_mod.ocr_document(str(tmp_path))
    assert r.error.code == "not_a_file"


@pytest.mark.asyncio
async def test_ocr_bad_extension_and_range(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    assert (await ocr_mod.ocr_document(str(f))).error.code == "unsupported_format"
    g = tmp_path / "x.png"
    g.write_bytes(b"x")
    assert (await ocr_mod.ocr_document(str(g), first_page=3, last_page=1)).error.code == "bad_page_range"


@pytest.mark.asyncio
async def test_speak_rejects_non_wav_output_path(tmp_path):
    r = await speak_mod.speak("नमस्ते", "hi", str(tmp_path / "a.mp3"))
    assert r.error.code == "bad_output_path" and ".wav" in r.error.message
