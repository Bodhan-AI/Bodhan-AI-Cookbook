"""Test standalone MCP server tool registration, schemas, and malformed input handling.
Verifies that all tools return structured error payloads and never crash the process.
"""

import json
from bodhan_mcp.server import mcp_server


def test_mcp_tools_registered():
    tools = mcp_server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]

    expected_tools = [
        "bodhan_transcribe",
        "bodhan_translate",
        "bodhan_speak",
        "bodhan_ocr",
        "bodhan_detect_language",
        "bodhan_list_voices",
        "bodhan_inspect_media",
    ]

    for et in expected_tools:
        assert et in tool_names, f"Expected tool '{et}' to be registered in MCPServer"


def test_mcp_transcribe_malformed_input():
    tool = mcp_server._tool_manager.get_tool("bodhan_transcribe")
    raw_res = tool.fn(audio_path="non_existent_recording_9999.wav")
    data = json.loads(raw_res)

    assert data["success"] is False
    assert data["error_code"] == "FILE_NOT_FOUND"
    assert "not found" in data["message"].lower()


def test_mcp_translate_malformed_input():
    tool = mcp_server._tool_manager.get_tool("bodhan_translate")

    # Empty text
    res1 = json.loads(tool.fn(text="", target_language_code="hi"))
    assert res1["success"] is False
    assert res1["error_code"] == "EMPTY_TEXT"

    # Unsupported language
    res2 = json.loads(tool.fn(text="Hello", target_language_code="german"))
    assert res2["success"] is False
    assert res2["error_code"] == "UNSUPPORTED_LANGUAGE"


def test_mcp_speak_malformed_input():
    tool = mcp_server._tool_manager.get_tool("bodhan_speak")
    res = json.loads(tool.fn(text="   ", language="hi"))

    assert res["success"] is False
    assert res["error_code"] == "EMPTY_TEXT"


def test_mcp_ocr_malformed_input():
    tool = mcp_server._tool_manager.get_tool("bodhan_ocr")

    # Non-existent file
    res1 = json.loads(tool.fn(file_path="non_existent_doc_888.pdf"))
    assert res1["success"] is False
    assert res1["error_code"] == "FILE_NOT_FOUND"

    # Unsupported format
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
        tmp_exe = f.name
    try:
        res2 = json.loads(tool.fn(file_path=tmp_exe))
        assert res2["success"] is False
        assert res2["error_code"] == "UNSUPPORTED_FORMAT"
    finally:
        import os
        if os.path.exists(tmp_exe):
            os.remove(tmp_exe)


def test_mcp_inspect_media_malformed_input():
    tool = mcp_server._tool_manager.get_tool("bodhan_inspect_media")
    res = json.loads(tool.fn(file_path="ghost_audio.mp3"))

    assert res["success"] is False
    assert res["error_code"] == "FILE_NOT_FOUND"


def test_mcp_list_voices_valid():
    tool = mcp_server._tool_manager.get_tool("bodhan_list_voices")
    res = json.loads(tool.fn(language="ta"))

    assert isinstance(res, list)
    assert len(res) >= 1
    assert any(v["name"] == "Kavitha" for v in res)


def test_mcp_detect_language_valid():
    tool = mcp_server._tool_manager.get_tool("bodhan_detect_language")
    res = json.loads(tool.fn(text="வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?"))

    assert res["language"] == "ta"
    assert res["script"] == "Tamil"
    assert res["confidence"] >= 0.90


