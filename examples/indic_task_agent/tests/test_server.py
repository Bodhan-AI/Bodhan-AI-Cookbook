from pathlib import Path

from bodhan_mcp.fake_client import FakeBodhanClient
from bodhan_mcp.server import create_server, inspect_media, list_voices, make_subtitles


def test_server_registers_only_the_phase_two_tools():
    server = create_server(FakeBodhanClient())
    tools = getattr(server._tool_manager, "_tools", {})
    names = set(tools)
    assert names == {
        "bodhan_transcribe",
        "bodhan_translate",
        "bodhan_synthesize",
        "bodhan_ocr",
        "inspect_media",
        "list_voices",
        "check_language_support",
        "make_subtitles",
    }


def test_helpers_are_deterministic(tmp_path):
    missing = inspect_media(str(tmp_path / "missing.wav"))
    assert missing["ok"] is False
    assert missing["error"]["code"] == "FILE_NOT_FOUND"

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert inspect_media(str(pdf))["kind"] == "pdf"

    voices = list_voices("ta", "female")
    assert voices["ok"] is True
    assert voices["voice"] == "Anitha"


def test_make_subtitles_returns_srt():
    result = make_subtitles([{"start": 0.0, "end": 1.0, "text": "Hello"}])
    assert result["ok"] is True
    assert "00:00:00,000 --> 00:00:01,000" in result["srt"]
