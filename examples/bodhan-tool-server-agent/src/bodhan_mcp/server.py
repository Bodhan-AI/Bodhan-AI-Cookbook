"""Bodhan MCP Server over stdio.

Exposes Bodhan's Indic models as standard Model Context Protocol (MCP) tools:
- bodhan_transcribe: Indic-Transcribe (Core & Flex) with audio windowing
- bodhan_translate: Indic-Translate (22+ languages) with format preservation
- bodhan_speak: Indic-Speak with short-input chunking & AI disclaimer
- bodhan_ocr: Indic-OCR for images and multi-page PDFs
- bodhan_inspect_media: File reference validation and media inspection
- bodhan_chunk_audio: Pre-split audio into sub-30s windows
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Compatible import across MCP v1 and v2
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP as MCPServer
    except ImportError:
        MCPServer = None

from bodhan_mcp.tools.media import chunk_audio_file, inspect_media_file
from bodhan_mcp.tools.ocr import extract_document_text
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text

logger = logging.getLogger("bodhan.mcp.server")


def create_mcp_server() -> Any:
    """Create and configure the Bodhan MCPServer with all tools registered."""
    if MCPServer is None:
        raise RuntimeError("mcp package is not installed. Please run `pip install mcp`.")

    server = MCPServer(
        name="bodhan-mcp",
        instructions="Bodhan Indic AI Tools: Indic-Transcribe, Indic-Translate, Indic-Speak, and Indic-OCR.",
    )

    @server.tool(
        name="bodhan_transcribe",
        description=(
            "Transcribe speech audio into text using Bodhan Indic-Transcribe (Core/Flex). "
            "Handles audio files of any length with automatic <=30s windowing and transcript merging. "
            "Pass audio by local file path (never inline base64). "
            "Supports 22 scheduled Indian languages and English."
        ),
    )
    async def tool_transcribe(
        audio_path: str,
        language: str = "auto",
        model_variant: str = "core",
    ) -> str:
        res = await transcribe_audio(
            audio_path=audio_path,
            language=language,
            model_variant=model_variant,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @server.tool(
        name="bodhan_translate",
        description=(
            "Translate text across 22 scheduled Indian languages and English using Bodhan Indic-Translate. "
            "Preserves document structure, Markdown formatting, LaTeX formulas, code blocks, and tables. "
            "Accepts raw text or text_path to a local file."
        ),
    )
    async def tool_translate(
        text: str = "",
        text_path: str = "",
        source_lang: str = "en",
        target_lang: str = "hi",
    ) -> str:
        res = await translate_text(
            text=text,
            text_path=text_path if text_path else None,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @server.tool(
        name="bodhan_speak",
        description=(
            "Synthesize teacher-paced, natural speech audio from Indian language text using Bodhan Indic-Speak. "
            "Handles STEM notation, numbers, fractions, and mixed scripts. "
            "Splits text into natural sentence chunks and outputs labeled AI-generated audio (.wav)."
        ),
    )
    async def tool_speak(
        text: str = "",
        text_path: str = "",
        language: str = "hi",
        voice: str = "default",
        output_path: str = "",
    ) -> str:
        res = await synthesize_speech(
            text=text,
            text_path=text_path if text_path else None,
            language=language,
            voice=voice,
            output_path=output_path if output_path else None,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @server.tool(
        name="bodhan_ocr",
        description=(
            "Digitize printed or handwritten documents from images (.png, .jpg, .webp) or multi-page PDFs (.pdf) "
            "using Bodhan Indic-OCR. "
            "Extracts reading-ordered Markdown preserving tables and equations. "
            "Pass document by local file path (never inline base64)."
        ),
    )
    async def tool_ocr(
        file_path: str,
        output_path: str = "",
    ) -> str:
        res = await extract_document_text(
            file_path=file_path,
            output_path=output_path if output_path else None,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    @server.tool(
        name="bodhan_inspect_media",
        description=(
            "Inspect media file properties (format, duration, channels, sample rate, dimensions, page count). "
            "Validates file reference before invoking generative models."
        ),
    )
    async def tool_inspect_media(file_path: str) -> str:
        res = inspect_media_file(file_path)
        return json.dumps(res, indent=2, ensure_ascii=False)

    @server.tool(
        name="bodhan_chunk_audio",
        description=(
            "Split an audio file into sub-30-second window slices for Bodhan Indic-Transcribe compliance. "
            "Returns chunk paths and timestamps."
        ),
    )
    async def tool_chunk_audio(
        audio_path: str,
        max_duration_sec: float = 30.0,
        output_dir: str = "",
    ) -> str:
        res = chunk_audio_file(
            audio_path=audio_path,
            max_duration_sec=max_duration_sec,
            output_dir=output_dir if output_dir else None,
        )
        return json.dumps(res, indent=2, ensure_ascii=False)

    return server


# Direct in-process dispatcher for shared tool layer without subprocess overhead
TOOL_DISPATCHER = {
    "bodhan_transcribe": transcribe_audio,
    "bodhan_translate": translate_text,
    "bodhan_speak": synthesize_speech,
    "bodhan_ocr": extract_document_text,
    "bodhan_inspect_media": lambda file_path: inspect_media_file(file_path),
    "bodhan_chunk_audio": lambda audio_path, max_duration_sec=30.0, output_dir=None: chunk_audio_file(
        audio_path, max_duration_sec, output_dir
    ),
}


async def dispatch_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a registered Bodhan tool directly in-process with typed arguments."""
    clean_name = tool_name.lower().replace("-", "_")
    if clean_name not in TOOL_DISPATCHER:
        raise ValueError(
            f"Unknown Bodhan tool '{tool_name}'. Available tools: {list(TOOL_DISPATCHER.keys())}"
        )

    handler = TOOL_DISPATCHER[clean_name]
    if asyncio.iscoroutinefunction(handler):
        result = await handler(**arguments)
    else:
        result = handler(**arguments)

    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:
            return {"result": result}
    return result


def run_stdio_server() -> None:
    """Run the Bodhan MCP server listening on stdio."""
    server = create_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    run_stdio_server()
