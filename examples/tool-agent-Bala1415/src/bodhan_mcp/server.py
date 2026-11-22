"""
Bodhan MCP Server — stdio entry point.

Run with: python -m bodhan_mcp

Exposes Bodhan's four Indic models as MCP tools over stdio transport,
so any MCP client can use them without the task agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load .env before anything else
load_dotenv()

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from bodhan_mcp.client import (
    BodhanClient,
    CostTracker,
    ResponseCache,
    BodhanAPIError,
    BudgetExceededError,
    UnsupportedLanguageError,
    SUPPORTED_LANGUAGE_CODES,
)
from bodhan_mcp.tools.transcribe import transcribe_audio, generate_srt
from bodhan_mcp.tools.translate import translate_text, get_supported_languages
from bodhan_mcp.tools.speak import synthesize_speech, list_voices
from bodhan_mcp.tools.ocr import extract_text_ocr
from bodhan_mcp.helpers.detect_language import detect_language
from bodhan_mcp.helpers.inspect_media import inspect_media

logger = logging.getLogger("bodhan_mcp")


def create_server() -> Server:
    """Create and configure the MCP server with all tools."""

    server = Server("bodhan-mcp")
    cost_tracker = CostTracker(max_cost_inr=float(os.getenv("DEFAULT_MAX_COST_INR", "20")))
    cache = ResponseCache()
    client = BodhanClient(cost_tracker=cost_tracker, cache=cache)

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="transcribe_audio",
                description=(
                    "Transcribe an audio file to text using Bodhan Indic-Transcribe. "
                    "Supports WAV, MP3, FLAC, and OGG. Automatically chunks audio longer "
                    "than 30 seconds. Returns timestamped segments. "
                    "Cost: ₹0.10 per minute of audio."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the audio file.",
                        },
                        "language": {
                            "type": "string",
                            "description": (
                                "Optional ISO 639-1 language hint (e.g., 'hi' for Hindi, "
                                "'ta' for Tamil). If omitted, the model auto-detects."
                            ),
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="translate_text",
                description=(
                    "Translate text between English and 22 scheduled Indian languages "
                    "using Bodhan Indic-Translate. Preserves Markdown, tables, and LaTeX. "
                    "Automatically chunks text exceeding the 32K token context window. "
                    "Supported languages: English (en), Hindi (hi), Bengali (bn), Tamil (ta), "
                    "Telugu (te), Marathi (mr), Gujarati (gu), Kannada (kn), Malayalam (ml), "
                    "Punjabi (pa), Odia (or), Assamese (as), Urdu (ur), Nepali (ne), "
                    "Sanskrit (sa), Sindhi (sd), Konkani (kok), Dogri (doi), Maithili (mai), "
                    "Bodo (brx), Santali (sat), Kashmiri (ks), Manipuri (mni). "
                    "Does NOT support French, Spanish, German, Chinese, Japanese, Korean, "
                    "or any other non-Indian language. "
                    "Cost: ₹0.20 per 10,000 output tokens."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The text to translate.",
                        },
                        "source_language": {
                            "type": "string",
                            "description": "ISO 639-1 code of the source language (e.g., 'en', 'hi').",
                        },
                        "target_language": {
                            "type": "string",
                            "description": "ISO 639-1 code of the target language (e.g., 'ta', 'mr').",
                        },
                    },
                    "required": ["text", "source_language", "target_language"],
                },
            ),
            Tool(
                name="synthesize_speech",
                description=(
                    "Convert text to speech using Bodhan Indic-Speak. "
                    "Supports 22 Indian languages and English with 45 voices. "
                    "Automatically chunks long text and concatenates audio. "
                    "WARNING: This is expensive (₹6 per 10,000 characters — 60x more "
                    "than translation). Minimize usage. "
                    "All generated audio is AI-generated, not a real human voice."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to convert to speech.",
                        },
                        "language": {
                            "type": "string",
                            "description": "ISO 639-1 language code (e.g., 'hi', 'ta', 'bn').",
                        },
                        "voice": {
                            "type": "string",
                            "description": (
                                "Voice name (e.g., 'Kavya', 'Kalpana', 'Prabhat'). "
                                "If omitted, a default voice is selected based on the language."
                            ),
                        },
                        "output_path": {
                            "type": "string",
                            "description": "File path to save the generated audio (WAV format).",
                        },
                        "style": {
                            "type": "string",
                            "description": "Speaking style: 'default', 'news', or 'conversational'.",
                            "default": "default",
                        },
                    },
                    "required": ["text", "language", "output_path"],
                },
            ),
            Tool(
                name="extract_text_ocr",
                description=(
                    "Extract text from an image or scanned PDF using Bodhan Indic-OCR. "
                    "Returns reading-ordered Markdown with tables preserved. "
                    "For PDFs, automatically converts each page to an image and processes "
                    "them one by one. Supports PNG, JPEG, and PDF files. "
                    "Cost: ₹0.20 per page/image."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the image (PNG/JPEG) or PDF file.",
                        },
                        "table_format": {
                            "type": "string",
                            "description": "Table output format: 'markdown' (default) or 'html'.",
                            "default": "markdown",
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="detect_language",
                description=(
                    "Detect the language of text using Unicode script analysis. "
                    "No API call required — fast and free. "
                    "Returns the language code, name, dominant script, and confidence. "
                    "For Devanagari text, distinguishes between Hindi, Marathi, Sanskrit, and Nepali."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The text to analyze for language detection.",
                        },
                    },
                    "required": ["text"],
                },
            ),
            Tool(
                name="inspect_media",
                description=(
                    "Inspect a file and return structured metadata. No API call required. "
                    "Returns: file type (audio/image/document/text/video), size, and "
                    "format-specific info such as duration (audio), page count (PDF), "
                    "dimensions (image), or character count (text)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Absolute path to the file to inspect.",
                        },
                    },
                    "required": ["file_path"],
                },
            ),
        ]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------
    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = await _dispatch_tool(client, name, arguments)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
        except (BodhanAPIError, UnsupportedLanguageError) as e:
            error_response = {
                "error": True,
                "error_type": type(e).__name__,
                "message": str(e),
                "model": getattr(e, "model", ""),
                "status_code": getattr(e, "status_code", None),
            }
            return [TextContent(type="text", text=json.dumps(error_response, indent=2))]
        except BudgetExceededError as e:
            error_response = {
                "error": True,
                "error_type": "BudgetExceededError",
                "message": str(e),
                "cost_summary": cost_tracker.summary(),
            }
            return [TextContent(type="text", text=json.dumps(error_response, indent=2))]
        except FileNotFoundError as e:
            error_response = {
                "error": True,
                "error_type": "FileNotFoundError",
                "message": str(e),
            }
            return [TextContent(type="text", text=json.dumps(error_response, indent=2))]
        except ValueError as e:
            error_response = {
                "error": True,
                "error_type": "ValueError",
                "message": str(e),
            }
            return [TextContent(type="text", text=json.dumps(error_response, indent=2))]
        except Exception as e:
            logger.exception(f"Unexpected error in tool {name}")
            error_response = {
                "error": True,
                "error_type": type(e).__name__,
                "message": f"Internal error: {str(e)}",
            }
            return [TextContent(type="text", text=json.dumps(error_response, indent=2))]

    return server


async def _dispatch_tool(client: BodhanClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route a tool call to the appropriate handler."""

    if name == "transcribe_audio":
        return await transcribe_audio(
            client=client,
            file_path=args["file_path"],
            language=args.get("language"),
        )
    elif name == "translate_text":
        return await translate_text(
            client=client,
            text=args["text"],
            source_language=args["source_language"],
            target_language=args["target_language"],
        )
    elif name == "synthesize_speech":
        return await synthesize_speech(
            client=client,
            text=args["text"],
            language=args["language"],
            voice=args.get("voice"),
            output_path=args["output_path"],
            style=args.get("style", "default"),
        )
    elif name == "extract_text_ocr":
        return await extract_text_ocr(
            client=client,
            file_path=args["file_path"],
            table_format=args.get("table_format", "markdown"),
        )
    elif name == "detect_language":
        return detect_language(args["text"])
    elif name == "inspect_media":
        return inspect_media(args["file_path"])
    else:
        raise ValueError(f"Unknown tool: {name}")


async def run_server():
    """Run the MCP server over stdio."""
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point for python -m bodhan_mcp."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,  # MCP uses stdout for protocol; logs go to stderr
    )
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
