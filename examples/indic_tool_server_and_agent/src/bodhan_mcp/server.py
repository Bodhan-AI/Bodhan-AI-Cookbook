"""FastMCP server exposing Bodhan Indic models and media helper tools over stdio."""
from __future__ import annotations

import logging
import sys
from typing import Optional, List, Any, Dict

# CRITICAL: logging to stderr only — stdout carries the MCP stdio protocol
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

from fastmcp import FastMCP

from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.schemas import (
    TranscribeAudioOutput,
    TranslateTextOutput,
    SynthesizeSpeechOutput,
    OCRDocumentOutput,
    InspectMediaOutput,
    DetectScriptOutput,
    BuildSRTOutput,
    SplitAudioOutput,
    ConcatAudioOutput,
    RenderPDFPagesOutput,
    GetSupportedLanguagesOutput,
    TranscriptSegment,
)
from bodhan_mcp.tools.stt import transcribe_audio as tool_transcribe_audio
from bodhan_mcp.tools.translate import translate_text as tool_translate_text
from bodhan_mcp.tools.tts import synthesize_speech as tool_synthesize_speech
from bodhan_mcp.tools.ocr import ocr_document as tool_ocr_document
from bodhan_mcp.tools.media import (
    inspect_media as tool_inspect_media,
    detect_script as tool_detect_script,
    build_srt as tool_build_srt,
    split_audio as tool_split_audio,
    concat_audio as tool_concat_audio,
    render_pdf_pages as tool_render_pdf_pages,
    get_supported_languages as tool_get_supported_languages,
)

logger = logging.getLogger("bodhan_mcp.server")

mcp = FastMCP(
    name="bodhan_mcp",
)


@mcp.tool(
    name="transcribe_audio",
    description=(
        "Transcribe speech from an audio file in any of 18 Indic languages using Indic-Transcribe.\n"
        "Automatically handles audio longer than 30s by chunking on silence/VAD.\n"
        "Supported languages: hi, bn, ta, te, mr, gu, kn, ml, pa, or, as, ur, sa, mai, bho, kok, ne, si.\n"
        "Cost: ₹0.10 per input audio minute. Input must be an existing local file path (pass by reference, never base64)."
    ),
)
async def transcribe_audio(
    file_path: str,
    language: Optional[str] = None,
    vad: bool = True,
) -> dict:
    try:
        res = await tool_transcribe_audio(file_path=file_path, language=language, vad=vad)
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        logger.exception("Unexpected error in transcribe_audio")
        return BodhanError("UPSTREAM_ERROR", f"Unexpected error: {exc}").to_dict()


@mcp.tool(
    name="translate_text",
    description=(
        "Translate text or markdown files between Indic languages and English using Indic-Translate.\n"
        "Automatically chunks long texts exceeding the 32K token limit, preserving Markdown tables.\n"
        "Supported targets: 18 Indic languages plus 'en' (English).\n"
        "Cost: ₹0.20 per 10,000 output tokens. Pass either 'text' or 'file_path'."
    ),
)
async def translate_text(
    text: Optional[str] = None,
    file_path: Optional[str] = None,
    target_language: str = "en",
    source_language: Optional[str] = None,
) -> dict:
    try:
        res = await tool_translate_text(
            text=text,
            file_path=file_path,
            target_language=target_language,
            source_language=source_language,
        )
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        logger.exception("Unexpected error in translate_text")
        return BodhanError("UPSTREAM_ERROR", f"Unexpected error: {exc}").to_dict()


@mcp.tool(
    name="synthesize_speech",
    description=(
        "Synthesize natural speech in an Indic language using Indic-Speak.\n"
        "Handles long text by chunking on sentence boundaries.\n"
        "Supported languages & voices:\n"
        "  hi (Amit, Priya), bn (Ravi, Ananya), ta (Surya, Devi), te (Arjun, Kavya),\n"
        "  mr (Vikram, Riya), gu (Kabir, Nisha), kn (Kiran, Deepa), ml (Dev, Meera), pa (Jeet, Simran).\n"
        "Cost: ₹6.00 per 10,000 input characters. Output saved to a WAV file."
    ),
)
async def synthesize_speech(
    text: str,
    language: str,
    voice: str,
    output_path: Optional[str] = None,
) -> dict:
    try:
        res = await tool_synthesize_speech(
            text=text,
            language=language,
            voice=voice,
            output_path=output_path,
        )
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        logger.exception("Unexpected error in synthesize_speech")
        return BodhanError("UPSTREAM_ERROR", f"Unexpected error: {exc}").to_dict()


@mcp.tool(
    name="ocr_document",
    description=(
        "Extract text from scanned documents, images (PNG, JPG, TIFF, BMP) or multi-page PDFs using Indic-OCR.\n"
        "Handles all Indic scripts and languages. Multi-page PDFs are rendered page-by-page.\n"
        "Cost: ₹0.20 per image / page. Returns per-page text and stitched full text."
    ),
)
async def ocr_document(
    file_path: str,
    output_dir: Optional[str] = None,
) -> dict:
    try:
        res = await tool_ocr_document(file_path=file_path, output_dir=output_dir)
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        logger.exception("Unexpected error in ocr_document")
        return BodhanError("UPSTREAM_ERROR", f"Unexpected error: {exc}").to_dict()


@mcp.tool(
    name="extract_text_from_image",
    description="Extract text from image or PDF document using Indic-OCR (alias for ocr_document).",
)
async def extract_text_from_image(
    image_path: str,
    out_dir: Optional[str] = None,
) -> dict:
    return await ocr_document(file_path=image_path, output_dir=out_dir)


@mcp.tool(
    name="inspect_media",
    description=(
        "Inspect any media file (audio, video, PDF, image, text) to obtain metadata:\n"
        "mime type, file size, duration in seconds, number of pages, image dimensions, or detected script.\n"
        "Fast, free helper tool."
    ),
)
async def inspect_media(file_path: str) -> dict:
    try:
        res = tool_inspect_media(file_path=file_path)
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Inspection failed: {exc}").to_dict()


@mcp.tool(
    name="detect_script",
    description=(
        "Detect the Indic script (Devanagari, Tamil, Telugu, etc.) and likely language from text sample.\n"
        "Heuristic analysis using Unicode block ranges. Fast, free helper tool."
    ),
)
async def detect_script(text: str) -> dict:
    try:
        res = tool_detect_script(text=text)
        return res.model_dump()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Detection failed: {exc}").to_dict()


@mcp.tool(
    name="build_srt",
    description=(
        "Build a standard .srt subtitle file from transcript segments with start and end timestamps, optionally translating subtitles to target_language."
    ),
)
async def build_srt(
    segments: List[dict],
    output_path: Optional[str] = None,
    target_language: Optional[str] = None,
    source_language: Optional[str] = None,
) -> dict:
    try:
        parsed_segs = [TranscriptSegment(**s) for s in segments]
        res = await tool_build_srt(
            segments=parsed_segs,
            output_path=output_path,
            target_language=target_language,
            source_language=source_language,
        )
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Failed to build SRT: {exc}").to_dict()


@mcp.tool(
    name="split_audio",
    description="Split an audio file into chunks of specified maximum duration (default 30s).",
)
async def split_audio(file_path: str, chunk_seconds: int = 30) -> dict:
    try:
        res = tool_split_audio(file_path=file_path, chunk_seconds=chunk_seconds)
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Failed to split audio: {exc}").to_dict()


@mcp.tool(
    name="concat_audio",
    description="Concatenate multiple audio files into a single audio file in sequence.",
)
async def concat_audio(file_paths: List[str], output_path: Optional[str] = None) -> dict:
    try:
        res = tool_concat_audio(file_paths=file_paths, output_path=output_path)
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Failed to concat audio: {exc}").to_dict()


@mcp.tool(
    name="render_pdf_pages",
    description="Render pages of a PDF document to PNG images.",
)
async def render_pdf_pages(file_path: str, dpi: int = 200, output_dir: Optional[str] = None) -> dict:
    try:
        res = tool_render_pdf_pages(file_path=file_path, dpi=dpi, output_dir=output_dir)
        return res.model_dump()
    except BodhanError as exc:
        return exc.to_dict()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Failed to render PDF: {exc}").to_dict()


@mcp.tool(
    name="get_supported_languages",
    description="Get supported Indic language codes and TTS voice mappings per model.",
)
async def get_supported_languages() -> dict:
    try:
        res = tool_get_supported_languages()
        return res.model_dump()
    except Exception as exc:
        return BodhanError("INVALID_INPUT", f"Failed to get languages: {exc}").to_dict()
