"""The MCP server runs on its own over stdio; text file helpers and the --files inventory."""

import sys
import wave
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bodhan_agent.inputs import build_inventory, task_message
from bodhan_mcp.tools.files import read_text_file, write_text_file


async def test_server_runs_standalone_over_stdio_with_any_mcp_client(tmp_path):
    params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env={"BODHAN_OUTPUT_ROOT": str(tmp_path)})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert {"translate_text", "transcribe_audio", "speak_text", "ocr_document", "detect_language"} <= names
        bad = await session.call_tool("transcribe_audio", {"audio_path": str(tmp_path / "missing.wav")})
        assert bad.structuredContent["ok"] is False and bad.structuredContent["error"]["code"] == "file_not_found"
        invalid = await session.call_tool("transcribe_audio", {})  # missing argument: an error result, not a crash
        assert invalid.isError
        out = await session.call_tool("write_text_file", {"output_path": "n.md", "content": "hi"})
        assert out.structuredContent["ok"] and (tmp_path / "n.md").read_text() == "hi"
        assert (await session.call_tool("detect_language", {"text": "வணக்கம்"})).structuredContent["language"] == "ta"


def test_read_text_file_pages_long_files(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("abcdefghij", encoding="utf-8")
    first = read_text_file(str(f), 0, 4)
    assert first.text == "abcd" and first.truncated and first.total_chars == 10
    assert read_text_file(str(f), 8, 4).text == "ij" and not read_text_file(str(f), 8, 4).truncated
    assert read_text_file(str(tmp_path / "no.txt")).error.code == "file_not_found"
    f.write_bytes(b"\xff\xfe\x00\x80")
    assert read_text_file(str(f)).error.code == "not_text"


def test_write_text_file_is_confined_to_the_output_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("BODHAN_OUTPUT_ROOT", str(root))
    ok = write_text_file("sub/a.srt", "1\n")
    assert ok.ok and (root / "sub" / "a.srt").read_text() == "1\n"
    for bad in (str(tmp_path / "evil.txt"), "../evil.txt"):
        assert write_text_file(bad, "x").error.code == "path_outside_output_root"


def test_inventory_lists_files_folders_and_missing_paths(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "note.txt").write_text("hi", encoding="utf-8")
    with wave.open(str(tmp_path / "in" / "clip.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 16000)
    inv = build_inventory([str(tmp_path / "in"), str(tmp_path / "gone.pdf")])
    by_name = {Path(e["path"]).name: e for e in inv}
    assert by_name["clip.wav"]["kind"] == "audio" and by_name["clip.wav"]["duration_s"] == 2.0
    assert by_name["note.txt"]["kind"] == "text" and by_name["gone.pdf"]["kind"] == "missing"
    msg = task_message("do it", inv, tmp_path / "artifacts", 30, 20.0)
    assert "[missing" in msg and "Rs 20.0" in msg and "never paste file contents" in msg
    assert "No input files" in task_message("t", build_inventory(None), tmp_path, 1, 1.0)
