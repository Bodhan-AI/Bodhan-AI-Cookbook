"""Standalone MCP Server for Bodhan AI Indic models.
Can be executed over stdio via: python -m bodhan_mcp
"""

import json
from typing import List, Optional
from mcp.server.mcpserver import MCPServer
from bodhan_mcp.types import ToolError
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text, translate_subtitles_batch
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.ocr import run_ocr
from bodhan_mcp.tools.lang_detect import detect_text_language
from bodhan_mcp.tools.voices import list_voices
from bodhan_mcp.audio_helpers import get_audio_info

# Initialize MCPServer instance
mcp_server = MCPServer(
    name="bodhan_mcp",
    instructions="Bodhan AI Indic language tool server (Indic-Transcribe, Indic-Translate, Indic-Speak, Indic-OCR)",
)


@mcp_server.tool()
def bodhan_transcribe(
    audio_path: str,
    language: Optional[str] = None,
    vad: bool = True,
    output_format: str = "text",
    output_srt_path: Optional[str] = None,
) -> str:
    """Transcribe speech to text using Indic-Transcribe.
    Handles audio files of any length with automatic >28s chunking at natural pauses.
    Supports output_format='text' or 'srt'. Returns JSON result with text, srt_path, duration, and cost.
    """
    try:
        res = transcribe_audio(
            audio_path=audio_path,
            language=language,
            vad=vad,
            output_format=output_format,
            output_srt_path=output_srt_path,
        )
        return res.model_dump_json()
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


@mcp_server.tool()
def bodhan_translate(
    text: str,
    target_language_code: str,
    source_language_code: Optional[str] = None,
    preserve_formatting: bool = True,
) -> str:
    """Translate text between Indian languages and English using Indic-Translate.
    Preserves Markdown table structures and bullet points.
    Returns JSON result with translated_text, token count, and cost.
    """
    try:
        res = translate_text(
            text=text,
            target_language_code=target_language_code,
            source_language_code=source_language_code,
            preserve_formatting=preserve_formatting,
        )
        return res.model_dump_json()
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


@mcp_server.tool()
def bodhan_speak(
    language: str,
    text: Optional[str] = None,
    voice: str = "Amit",
    output_path: Optional[str] = None,
    text_artifact_id: Optional[str] = None,
) -> str:
    """Synthesize speech using Indic-Speak.
    Handles long text by clause splitting and MP3 stitching.
    Supports passing text by reference via text_artifact_id.
    Returns JSON result with audio_path (MP3), duration, character count, and cost.
    """
    try:
        res = synthesize_speech(
            text=text,
            language=language,
            voice=voice,
            output_path=output_path,
            text_artifact_id=text_artifact_id,
        )
        return res.model_dump_json()
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


@mcp_server.tool()
def bodhan_ocr(
    file_path: str,
    page_numbers: Optional[List[int]] = None,
) -> str:
    """Extract text and tables from images and scanned PDF documents using Indic-OCR.
    Handles multi-page PDFs using PyMuPDF page rendering.
    Returns JSON result with extracted markdown text, table count, and cost.
    """
    try:
        res = run_ocr(
            file_path=file_path,
            page_numbers=page_numbers,
        )
        return res.model_dump_json()
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


@mcp_server.tool()
def bodhan_detect_language(text: str) -> str:
    """Detect language and script from text using Unicode ranges and Devanagari heuristics.
    Returns JSON result with language, script, confidence, and whether the language was assumed.
    """
    try:
        res = detect_text_language(text)
        return res.model_dump_json()
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


@mcp_server.tool()
def bodhan_list_voices(language: Optional[str] = None, gender: Optional[str] = None) -> str:
    """List supported voices from official Bodhan documentation filtered by language and gender."""
    try:
        voices = list_voices(language=language, gender=gender)
        return json.dumps([v.model_dump() for v in voices], indent=2)
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


@mcp_server.tool()
def bodhan_inspect_media(file_path: str) -> str:
    """Inspect audio properties (duration, samplerate, channels)."""
    try:
        info = get_audio_info(file_path)
        return json.dumps(info, indent=2)
    except ToolError as e:
        return json.dumps(e.to_dict())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "INTERNAL_ERROR", "message": str(e), "retryable": False})


async def run_stdio_server() -> None:
    """Run the MCP server over stdio with Windows-safe explicit stream wrapping.
    Passing explicit stdin/stdout streams skips internal file-descriptor redirection
    (_claim_fd) on Windows cleanly without requiring any site-packages patches.
    """
    import sys
    import anyio
    from mcp.server.stdio import stdio_server, _UnownedTextWrapper

    stdin = anyio.wrap_file(_UnownedTextWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    stdout = anyio.wrap_file(_UnownedTextWrapper(sys.stdout.buffer, encoding="utf-8"))

    async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
        await mcp_server._lowlevel_server.run(
            read_stream,
            write_stream,
            mcp_server._lowlevel_server.create_initialization_options(),
        )


def main():
    """Run the MCP server over stdio."""
    import asyncio
    asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()

