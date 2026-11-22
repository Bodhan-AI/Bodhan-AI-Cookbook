#!/usr/bin/env python3
"""End-to-end stdio subprocess MCP client tests.

Spawns ``python -m bodhan_mcp`` as a real subprocess over stdio using the
official ``mcp`` client SDK and drives initialize / list_tools / list_resources
/ list_prompts / malformed tool calls exactly as a third-party MCP client would.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Dummy creds so AsyncOpenAI/Bodhan client construction is stable in-process.
os.environ.setdefault("LLM_BASE_URL", "https://api.openai.com/v1")
os.environ.setdefault("LLM_API_KEY", "sk-dummy-test-key")
os.environ.setdefault("LLM_MODEL", "gpt-4o-mini")


_SRC = Path(__file__).resolve().parent.parent / "src"
_ROOT = Path(__file__).resolve().parent.parent


def _params() -> StdioServerParameters:
    env = {**os.environ}
    env["PYTHONPATH"] = str(_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        cwd=str(_ROOT),
        env=env,
    )


def _make_wav(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=1", "-ar", "16000", "-ac", "1",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


@pytest.fixture
def session():
    """Live stdio subprocess MCP session, torn down cleanly after each test."""
    async def _run(body):
        async with stdio_client(_params()) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                return await body(s)

    return _run


def _error_code(result) -> str:
    """Extract the structured error code from a CallToolResult."""
    if not result.content:
        return ""
    text = getattr(result.content[0], "text", "") or ""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text[:80]
    return data.get("code") or data.get("error") or ""


class TestProtocol:
    @pytest.mark.asyncio
    async def test_initialize(self, session):
        async def _check(s):
            assert s is not None

        await session(_check)

    @pytest.mark.asyncio
    async def test_list_tools_has_5_typed_schemas(self, session):
        async def _check(s):
            tools = await s.list_tools()
            assert len(tools.tools) == 5
            names = {t.name for t in tools.tools}
            assert names == {
                "inspect_media", "transcribe_audio", "extract_document_text",
                "translate_text", "synthesize_speech",
            }
            for t in tools.tools:
                assert t.description and t.description.strip()
                schema = t.inputSchema
                assert schema.get("type") == "object"
                assert schema.get("properties")
            return tools

        await session(_check)

    @pytest.mark.asyncio
    async def test_list_resources(self, session):
        async def _check(s):
            res = await s.list_resources()
            uris = {str(r.uri) for r in res.resources}
            assert {"bodhan://capabilities", "bodhan://voices"} <= uris
            return res

        await session(_check)

    @pytest.mark.asyncio
    async def test_list_prompts(self, session):
        async def _check(s):
            prompts = await s.list_prompts()
            names = {p.name for p in prompts.prompts}
            assert names == {
                "document_to_indic_audio",
                "media_to_english_subtitles",
                "preserve_table_translation",
            }
            return prompts

        await session(_check)


class TestMalformedCallsOverStdio:
    @pytest.mark.asyncio
    async def test_file_not_found(self, session):
        with tempfile.TemporaryDirectory() as d:
            missing = str(Path(d) / "missing.wav")

            async def _check(s):
                res = await s.call_tool("inspect_media", {"file_path": missing})
                assert _error_code(res) == "FILE_NOT_FOUND"
                # Server stays alive after a malformed call
                tools = await s.list_tools()
                assert len(tools.tools) == 5

            await session(_check)

    @pytest.mark.asyncio
    async def test_invalid_input_directory(self, session):
        with tempfile.TemporaryDirectory() as d:
            async def _check(s):
                res = await s.call_tool(
                    "inspect_media", {"file_path": d}
                )
                assert _error_code(res) == "INVALID_INPUT"
                tools = await s.list_tools()
                assert len(tools.tools) == 5

            await session(_check)

    @pytest.mark.asyncio
    async def test_unsupported_format(self, session):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "x.exe"
            exe.write_bytes(b"x")

            async def _check(s):
                res = await s.call_tool(
                    "inspect_media", {"file_path": str(exe)}
                )
                assert _error_code(res) == "UNSUPPORTED_FORMAT"
                tools = await s.list_tools()
                assert len(tools.tools) == 5

            await session(_check)

    @pytest.mark.asyncio
    async def test_empty_input(self, session):
        async def _check(s):
            res = await s.call_tool(
                "translate_text",
                {"source_lang": "hi", "target_lang": "ta", "text": "   "},
            )
            assert _error_code(res) == "EMPTY_INPUT"
            tools = await s.list_tools()
            assert len(tools.tools) == 5

        await session(_check)

    @pytest.mark.asyncio
    async def test_unsupported_language(self, session):
        async def _check(s):
            res = await s.call_tool(
                "translate_text",
                {"source_lang": "fr", "target_lang": "hi", "text": "hello"},
            )
            assert _error_code(res) == "UNSUPPORTED_LANGUAGE"
            tools = await s.list_tools()
            assert len(tools.tools) == 5

        await session(_check)

    @pytest.mark.asyncio
    async def test_invalid_page_range(self, session):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "doc.pdf"
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument.new()
            for _ in range(3):
                doc.new_page(width=612, height=792)
            doc.save(str(pdf))
            doc.close()

            async def _check(s):
                for bad in ("0", "5-2", "abc"):
                    res = await s.call_tool(
                        "extract_document_text",
                        {"file_path": str(pdf), "page_range": bad},
                    )
                    assert _error_code(res) == "INVALID_PAGE_RANGE", bad
                tools = await s.list_tools()
                assert len(tools.tools) == 5

            await session(_check)

    @pytest.mark.asyncio
    async def test_invalid_parameter(self, session):
        with tempfile.TemporaryDirectory() as d:
            wav = _make_wav(Path(d) / "a.wav")

            async def _check(s):
                res = await s.call_tool(
                    "transcribe_audio",
                    {"file_path": str(wav), "max_duration_sec": 0},
                )
                assert _error_code(res) == "INVALID_PARAMETER"
                tools = await s.list_tools()
                assert len(tools.tools) == 5

            await session(_check)

    @pytest.mark.asyncio
    async def test_invalid_voice(self, session):
        async def _check(s):
            res = await s.call_tool(
                "synthesize_speech",
                {
                    "language": "hi",
                    "output_file": "out.wav",
                    "text": "\u0928\u092e\u0938\u094d\u0924\u0947",
                    "gender": "robot",
                },
            )
            assert _error_code(res) == "INVALID_VOICE"
            tools = await s.list_tools()
            assert len(tools.tools) == 5

        await session(_check)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
