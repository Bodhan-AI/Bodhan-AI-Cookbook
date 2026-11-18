"""Tests for MCP tool error handling on bad or invalid inputs."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from bodhan_mcp.server import mcp
from bodhan_mcp.core.errors import (
    FILE_NOT_FOUND,
    INVALID_INPUT,
    UNSUPPORTED_LANGUAGE,
)


async def _call_tool_dict(name: str, args: dict) -> dict:
    """Helper to call FastMCP tool and extract structured response dict."""
    res = await mcp.call_tool(name, args)
    if hasattr(res, "structured_content") and res.structured_content:
        return res.structured_content
    # Fallback parse text
    if hasattr(res, "content") and res.content:
        return json.loads(res.content[0].text)
    return {}


@pytest.mark.asyncio
async def test_transcribe_nonexistent_file():
    """Calling transcribe_audio with a nonexistent path returns FILE_NOT_FOUND error."""
    out = await _call_tool_dict("transcribe_audio", {"file_path": "/path/does/not/exist.wav"})
    assert out.get("ok") is False
    assert out["error"]["code"] == FILE_NOT_FOUND
    assert out["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_transcribe_unsupported_language(tmp_path: Path):
    """Calling transcribe_audio with unsupported language (e.g. French) returns UNSUPPORTED_LANGUAGE."""
    fake_wav = tmp_path / "valid.wav"
    fake_wav.write_bytes(b"RIFF" + b"\x00" * 40)  # dummy header

    out = await _call_tool_dict("transcribe_audio", {"file_path": str(fake_wav), "language": "fr"})
    assert out.get("ok") is False
    assert out["error"]["code"] == UNSUPPORTED_LANGUAGE


@pytest.mark.asyncio
async def test_translate_missing_inputs():
    """Calling translate_text without text or file_path returns INVALID_INPUT."""
    out = await _call_tool_dict("translate_text", {"target_language": "hi"})
    assert out.get("ok") is False
    assert out["error"]["code"] == INVALID_INPUT


@pytest.mark.asyncio
async def test_translate_unsupported_language():
    """Calling translate_text with unsupported target language (e.g. French) returns UNSUPPORTED_LANGUAGE."""
    out = await _call_tool_dict(
        "translate_text",
        {"text": "Hello world", "target_language": "fr"},
    )
    assert out.get("ok") is False
    assert out["error"]["code"] == UNSUPPORTED_LANGUAGE
    assert "hint" in out["error"]


@pytest.mark.asyncio
async def test_synthesize_unsupported_language():
    """Calling synthesize_speech with unsupported language returns error without crashing."""
    out = await _call_tool_dict(
        "synthesize_speech",
        {"text": "Bonjour", "language": "fr", "voice": "Amit"},
    )
    assert out.get("ok") is False
    assert out["error"]["code"] in {UNSUPPORTED_LANGUAGE, INVALID_INPUT}


@pytest.mark.asyncio
async def test_ocr_nonexistent_file():
    """Calling ocr_document with nonexistent path returns FILE_NOT_FOUND."""
    out = await _call_tool_dict("ocr_document", {"file_path": "/nonexistent/doc.pdf"})
    assert out.get("ok") is False
    assert out["error"]["code"] == FILE_NOT_FOUND


@pytest.mark.asyncio
async def test_server_continues_running_after_multiple_bad_calls():
    """Server maintains operational state after repeated bad input calls."""
    # Bad call 1
    r1 = await _call_tool_dict("transcribe_audio", {"file_path": "/bad/1.wav"})
    assert r1.get("ok") is False

    # Bad call 2
    r2 = await _call_tool_dict("translate_text", {"target_language": "spanish"})
    assert r2.get("ok") is False

    # Valid call to detect_script continues to work normally
    r3 = await _call_tool_dict("detect_script", {"text": "नमस्ते"})
    assert r3.get("ok") is True
    assert r3.get("script_name") == "Devanagari"
