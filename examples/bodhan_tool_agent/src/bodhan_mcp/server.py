"""Bodhan Model Context Protocol (MCP) Server.

Exposes Bodhan AI speech-to-text, text-to-speech, translation, and OCR models as
structured, typed tools with metadata resources, prompts, and transport adapters.
Default transport: STDIO. Optional: Streamable HTTP with Bearer token authentication.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Literal
from mcp.server.fastmcp import FastMCP

from bodhan_mcp.errors import BodhanError
from bodhan_mcp.resources import (
    get_languages_resource,
    get_pricing_resource,
    get_styles_resource,
    get_voices_resource,
)
from bodhan_mcp.schemas import (
    ExtractTextFromDocumentOutput,
    SynthesizeSpeechOutput,
    TranscribeAudioOutput,
    TranslateTextOutput,
)
from bodhan_mcp.tools.ocr import extract_text_from_document
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text

# Configure diagnostics to stderr to keep stdout 100% clean for STDIO MCP frames
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [bodhan_mcp] %(message)s",
)
logger = logging.getLogger("bodhan_mcp")

# Initialize FastMCP Server
mcp_server = FastMCP(
    name="Bodhan MCP Tool Server",
    instructions=(
        "Bodhan MCP Server provides high-precision AI capabilities for 25 Indian languages + English: "
        "speech-to-text (indic-transcribe), translation (indic-translate), "
        "text-to-speech (indic-speak), and document OCR (indic-ocr)."
    ),
)


# ---------------------------------------------------------------------------
# Core Tools
# ---------------------------------------------------------------------------

@mcp_server.tool(
    name="transcribe_audio",
    description=(
        "Transcribe local audio files (WAV, MP3, M4A, OGG, FLAC) into text using Bodhan indic-transcribe. "
        "Supports 25 Indian languages and English. Automatically chunks audio files longer than 28 seconds "
        "and reconstructs the complete transcript in sequence with timestamp segments. "
        "Returns complete text, detected/specified language, duration in seconds, and INR cost."
    ),
)
def tool_transcribe_audio(
    file_path: str,
    language: str | None = None,
) -> dict[str, Any]:
    """Transcribe audio file via Bodhan indic-transcribe."""
    try:
        out: TranscribeAudioOutput = transcribe_audio(file_path=file_path, language=language)
        return out.model_dump()
    except BodhanError as e:
        logger.error(f"transcribe_audio failed: {e.message}")
        return {"success": False, "error": e.message, "details": e.to_dict()}
    except Exception as e:
        logger.exception("Unexpected error in transcribe_audio")
        return {"success": False, "error": f"Internal error: {e}"}


@mcp_server.tool(
    name="translate_text",
    description=(
        "Translate text into an Indian language or English using Bodhan indic-translate. "
        "Preserves Markdown formatting, headings, bullet lists, and paragraphs. "
        "Source language is detected automatically. Supported scripts: 'native' (default), "
        "'roman' (Latin letters), or 'codemix' (conversational Hinglish/Tanglish). "
        "Returns the translated text, source/output character counts, and INR cost."
    ),
)
def tool_translate_text(
    text: str,
    target_language: str,
    script: Literal["native", "roman", "codemix"] = "native",
    preserve_formatting: bool = True,
) -> dict[str, Any]:
    """Translate text via Bodhan indic-translate."""
    try:
        out: TranslateTextOutput = translate_text(
            text=text,
            target_language=target_language,
            script=script,
            preserve_formatting=preserve_formatting,
        )
        return out.model_dump()
    except BodhanError as e:
        logger.error(f"translate_text failed: {e.message}")
        return {"success": False, "error": e.message, "details": e.to_dict()}
    except Exception as e:
        logger.exception("Unexpected error in translate_text")
        return {"success": False, "error": f"Internal error: {e}"}


@mcp_server.tool(
    name="synthesize_speech",
    description=(
        "Synthesize text into natural human speech using Bodhan indic-speak. "
        "Supports 22 Indian languages and English, 45 native voices (female/male), and 14 styles "
        "(e.g., 'news', 'educational lecture', 'children\\'s stories', 'happy', 'sad'). "
        "Splits long text across sentence boundaries, synthesizes each, and stitches them "
        "into a valid 24 kHz mono WAV file. Returns audio file path, duration, and INR cost."
    ),
)
def tool_synthesize_speech(
    text: str,
    language: str,
    voice: str | None = None,
    gender_preference: Literal["female", "male"] = "female",
    style: str | None = None,
    output_file_path: str | None = None,
) -> dict[str, Any]:
    """Synthesize speech via Bodhan indic-speak."""
    try:
        out: SynthesizeSpeechOutput = synthesize_speech(
            text=text,
            language=language,
            voice=voice,
            gender_preference=gender_preference,
            style=style,
            output_file_path=output_file_path,
        )
        return out.model_dump()
    except BodhanError as e:
        logger.error(f"synthesize_speech failed: {e.message}")
        return {"success": False, "error": e.message, "details": e.to_dict()}
    except Exception as e:
        logger.exception("Unexpected error in synthesize_speech")
        return {"success": False, "error": f"Internal error: {e}"}


@mcp_server.tool(
    name="extract_text_from_document",
    description=(
        "Extract text, tables, and layout structure from images (PNG, JPG, JPEG) or multi-page PDFs "
        "using Bodhan indic-ocr. Handles script-agnostic Indian text and English. "
        "Rasterizes PDF pages and performs OCR on each page in order, returning complete "
        "Markdown text, page count, per-page bounding boxes, and INR cost."
    ),
)
def tool_extract_text_from_document(
    file_path: str,
    table_format: Literal["markdown", "html"] = "markdown",
) -> dict[str, Any]:
    """Extract text from document via Bodhan indic-ocr."""
    try:
        out: ExtractTextFromDocumentOutput = extract_text_from_document(
            file_path=file_path,
            table_format=table_format,
        )
        return out.model_dump()
    except BodhanError as e:
        logger.error(f"extract_text_from_document failed: {e.message}")
        return {"success": False, "error": e.message, "details": e.to_dict()}
    except Exception as e:
        logger.exception("Unexpected error in extract_text_from_document")
        return {"success": False, "error": f"Internal error: {e}"}


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp_server.resource("bodhan://metadata/languages")
def resource_languages() -> str:
    """Return JSON catalog of supported languages and model capabilities."""
    return get_languages_resource()


@mcp_server.resource("bodhan://metadata/voices")
def resource_voices() -> str:
    """Return JSON catalog of available Indic voices mapped by language and gender."""
    return get_voices_resource()


@mcp_server.resource("bodhan://metadata/styles")
def resource_styles() -> str:
    """Return JSON catalog of supported text-to-speech speaking styles."""
    return get_styles_resource()


@mcp_server.resource("bodhan://metadata/pricing")
def resource_pricing() -> str:
    """Return JSON catalog of the official Bodhan pricing schedule in INR."""
    return get_pricing_resource()


# ---------------------------------------------------------------------------
# Server Runners
# ---------------------------------------------------------------------------

def run_stdio():
    """Run the MCP server over standard I/O (default)."""
    logger.info("Starting Bodhan MCP server on STDIO transport...")
    mcp_server.run(transport="stdio")


def run_http(host: str = "127.0.0.1", port: int = 8000, bearer_token: str | None = None):
    """Run the MCP server over Streamable HTTP with optional Bearer token authentication."""
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    token = bearer_token or os.getenv("MCP_AUTH_TOKEN")
    logger.info(f"Starting Bodhan MCP server on HTTP transport at http://{host}:{port} (auth required: {bool(token)})")

    app = FastAPI(title="Bodhan MCP HTTP Server", version="0.1.0")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Health check endpoint is public
        if request.url.path == "/health":
            return await call_next(request)

        if token:
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Unauthorized: Missing Bearer token in Authorization header"},
                )
            incoming_token = auth_header.split(" ", 1)[1].strip()
            if incoming_token != token:
                return JSONResponse(
                    status_code=403,
                    content={"error": "Forbidden: Invalid Bearer token"},
                )

        return await call_next(request)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "bodhan_mcp"}

    @app.get("/resources/languages")
    def api_languages():
        return json.loads(get_languages_resource())

    @app.get("/resources/voices")
    def api_voices():
        return json.loads(get_voices_resource())

    @app.get("/resources/pricing")
    def api_pricing():
        return json.loads(get_pricing_resource())

    @app.post("/tools/transcribe_audio")
    def api_transcribe(payload: dict):
        return tool_transcribe_audio(**payload)

    @app.post("/tools/translate_text")
    def api_translate(payload: dict):
        return tool_translate_text(**payload)

    @app.post("/tools/synthesize_speech")
    def api_synthesize(payload: dict):
        return tool_synthesize_speech(**payload)

    @app.post("/tools/extract_text_from_document")
    def api_ocr(payload: dict):
        return tool_extract_text_from_document(**payload)

    uvicorn.run(app, host=host, port=port, log_level="warning")
