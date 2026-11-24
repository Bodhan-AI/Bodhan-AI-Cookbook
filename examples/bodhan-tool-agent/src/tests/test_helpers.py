from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from bodhan_mcp.languages import detect_script_language, normalize_lang_code
from bodhan_mcp.models import ErrorCode, ToolError
from bodhan_mcp.tools.capabilities import list_capabilities
from bodhan_mcp.tools.detect_language import detect_language
from bodhan_mcp.tools.inspect import inspect_media
from bodhan_mcp.workspace import Workspace


def test_normalize_lang_code():
    assert normalize_lang_code("Hindi") == "hi"
    assert normalize_lang_code("TA-in") == "ta"
    assert normalize_lang_code("en") == "en"

    with pytest.raises(ToolError) as exc_info:
        normalize_lang_code("klingon")
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_LANGUAGE


def test_detect_script_language_devanagari():
    text = "नमस्ते दुनिया"
    res = detect_script_language(text)
    assert res["script"] == "DEVANAGARI"
    assert res["ambiguous"] is True
    assert "hi" in res["candidates"]


def test_workspace_sandbox():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        # Create a safe file inside workspace
        safe_file = root / "test.txt"
        safe_file.write_text("Hello workspace", encoding="utf-8")

        resolved = ws.resolve_input("test.txt")
        assert resolved == safe_file.resolve()

        # Test out-of-bounds access
        with pytest.raises(ToolError) as exc_info:
            ws.resolve_input("../outside.txt")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED


def test_inspect_and_detect_language_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        ws = Workspace(root)

        txt_file = root / "sample.txt"
        txt_file.write_text("வணக்கம் உலகம்", encoding="utf-8")  # Tamil script

        # Test inspection
        inspect_res = inspect_media(ws, "sample.txt")
        assert inspect_res["ok"] is True
        assert inspect_res["data"]["file_type"] == "text"
        assert inspect_res["data"]["line_count"] == 1

        # Test language detection on file
        lang_res = detect_language("sample.txt", ws)
        assert lang_res["ok"] is True
        assert "ta" in lang_res["data"]["candidates"]


def test_list_capabilities():
    caps = list_capabilities()
    assert caps["ok"] is True
    assert "transcribe" in caps["data"]["models"]
    assert "hi" in caps["data"]["supported_languages"]

def test_detect_script_language_english_long_text():
    res = detect_script_language("This is a complete English sentence over ten characters.")
    assert res["script"] == "ENGLISH"
    assert res["candidates"] == ["en"]
    assert res["ambiguous"] is False
    assert res["confidence"] == 1.0
