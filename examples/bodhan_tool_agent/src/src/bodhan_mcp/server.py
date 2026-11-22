"""
MCP Server implementation exposing Bodhan Indic AI models as tools and resources.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List
from mcp.server.mcpserver import MCPServer
from mcp.types import TextContent

from .client import BodhanClient, BodhanAPIError
from .models import (
    TranscribeInput,
    TranslateInput,
    SpeakInput,
    OCRInput,
    InspectMediaInput,
    DetectLanguageInput,
    SUPPORTED_LANGUAGES,
)
from .tools.transcribe import run_transcribe
from .tools.translate import run_translate
from .tools.speak import run_speak
from .tools.ocr import run_ocr
from .tools.media import run_inspect_media, run_detect_language

logger = logging.getLogger(__name__)

# Initialize MCP Server
mcp_server = MCPServer(
    name="bodhan-indic-tools",
    version="0.1.0",
    description="MCP server exposing Bodhan Indic models: Indic-Transcribe, Indic-Translate, Indic-Speak, and Indic-OCR.",
)

_client: Optional[BodhanClient] = None


def get_client() -> BodhanClient:
    global _client
    if _client is None:
        _client = BodhanClient()
    return _client


# --- MCP TOOLS ---

@mcp_server.tool(
    name="transcribe_audio",
    description="Transcribe speech in an audio file using Indic-Transcribe. Handles files of any duration by automatically chunking audio into <=30s windows. Returns full text, detected language, timestamped segments, and cost in INR."
)
def transcribe_audio(
    audio_path: str,
    language: Optional[str] = None,
    model_variant: str = "core"
) -> str:
    """
    Transcribe speech to text.
    :param audio_path: Path to local audio file (WAV, MP3, etc.)
    :param language: ISO-639 language code (e.g. 'hi', 'ta', 'te'). Auto-detected if omitted.
    :param model_variant: 'core' or 'flex'
    """
    try:
        args = TranscribeInput(audio_path=audio_path, language=language, model_variant=model_variant)
        result = run_transcribe(args, get_client())
        return result.model_dump_json(indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": str(e),
            "tool": "transcribe_audio",
        }, indent=2)


@mcp_server.tool(
    name="translate_text",
    description="Translate text between Indian languages and English using Indic-Translate. Preserves Markdown formatting, tables, and LaTeX equations. Automatically splits long context while maintaining pacing."
)
def translate_text(
    text: str,
    target_lang: str,
    source_lang: Optional[str] = "en"
) -> str:
    """
    Translate text.
    :param text: Text to translate
    :param target_lang: Target ISO language code (e.g. 'hi', 'ta', 'bn', 'mr', 'te')
    :param source_lang: Source ISO language code (default 'en')
    """
    try:
        args = TranslateInput(text=text, target_lang=target_lang, source_lang=source_lang)
        result = run_translate(args, get_client())
        return result.model_dump_json(indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": str(e),
            "tool": "translate_text",
        }, indent=2)


@mcp_server.tool(
    name="synthesize_speech",
    description="Synthesize Indian-language speech from text using Indic-Speak. Automatically divides text into sentence chunks to conform to model constraints and concatenates into a complete audio file. Returns path to audio and cost in INR."
)
def synthesize_speech(
    text: str,
    language: str,
    output_audio_path: Optional[str] = None,
    gender: str = "female"
) -> str:
    """
    Synthesize speech audio from text.
    :param text: Text in an Indian language
    :param language: ISO language code (e.g. 'hi', 'ta', 'bn')
    :param output_audio_path: Optional target file path for generated audio (WAV/MP3)
    :param gender: 'female' or 'male'
    """
    try:
        args = SpeakInput(
            text=text,
            language=language,
            output_audio_path=output_audio_path,
            gender=gender,
        )
        result = run_speak(args, get_client())
        return result.model_dump_json(indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": str(e),
            "tool": "synthesize_speech",
        }, indent=2)


@mcp_server.tool(
    name="extract_text_ocr",
    description="Extract text, Markdown tables, and LaTeX equations from scanned documents (PDFs) or images (PNG, JPG) using Indic-OCR. Handles multi-page documents seamlessly."
)
def extract_text_ocr(
    file_path: str,
    extract_tables: bool = True,
    extract_equations: bool = True
) -> str:
    """
    Extract text via OCR.
    :param file_path: Path to image or PDF file
    :param extract_tables: Preserves Markdown table structures
    :param extract_equations: Preserves LaTeX equation formatting
    """
    try:
        args = OCRInput(
            file_path=file_path,
            extract_tables=extract_tables,
            extract_equations=extract_equations,
        )
        result = run_ocr(args, get_client())
        return result.model_dump_json(indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": str(e),
            "tool": "extract_text_ocr",
        }, indent=2)


@mcp_server.tool(
    name="inspect_media",
    description="Inspect media file technical metadata (type, duration, sample rate, channels, image dimensions, PDF page count) without loading media content into context."
)
def inspect_media(file_path: str) -> str:
    """
    Inspect media file metadata.
    :param file_path: Path to local media file
    """
    try:
        args = InspectMediaInput(file_path=file_path)
        result = run_inspect_media(args)
        return result.model_dump_json(indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": str(e),
            "tool": "inspect_media",
        }, indent=2)


@mcp_server.tool(
    name="detect_language",
    description="Detect language and script of a text snippet or audio clip. Checks for Indic script ranges and language support."
)
def detect_language(
    text: Optional[str] = None,
    audio_path: Optional[str] = None
) -> str:
    """
    Detect language.
    :param text: Optional text snippet
    :param audio_path: Optional audio file
    """
    try:
        args = DetectLanguageInput(text=text, audio_path=audio_path)
        result = run_detect_language(args, get_client())
        return result.model_dump_json(indent=2)
    except Exception as e:
        return json.dumps({
            "error": True,
            "message": str(e),
            "tool": "detect_language",
        }, indent=2)


# --- MCP RESOURCE (Stretch Goal) ---

@mcp_server.resource(
    uri="voices://library",
    name="Indic-Speak Voice Library",
    description="List of available voices and supported production languages for Indic-Speak"
)
def get_voice_library() -> str:
    library = {
        "provider": "Bodhan Indic-Speak",
        "supported_languages": SUPPORTED_LANGUAGES,
        "voices": [
            {"id": f"{code}_female", "language": code, "language_name": name, "gender": "female"}
            for code, name in SUPPORTED_LANGUAGES.items() if code != "en"
        ] + [
            {"id": f"{code}_male", "language": code, "language_name": name, "gender": "male"}
            for code, name in SUPPORTED_LANGUAGES.items() if code != "en"
        ],
    }
    return json.dumps(library, indent=2)


def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000, token: Optional[str] = None):
    """Run the MCP server over stdio or streamable SSE HTTP transport with token auth."""
    import asyncio
    if transport == "sse":
        print(f"Starting Bodhan MCP Server over SSE HTTP on {host}:{port}/sse (Auth token: {'Enabled' if token else 'None'})")
        # If token authentication requested
        if token:
            os.environ["BODHAN_MCP_AUTH_TOKEN"] = token
        asyncio.run(mcp_server.run_sse_async(host=host, port=port))
    else:
        asyncio.run(mcp_server.run_stdio_async())


if __name__ == "__main__":
    run_server()
