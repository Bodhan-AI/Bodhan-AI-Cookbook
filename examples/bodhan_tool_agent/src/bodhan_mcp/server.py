"""FastMCP server registration for Bodhan Indic models and helper tools."""

import json
import logging
import sys

from mcp.server.fastmcp import FastMCP

from bodhan_mcp import config, errors
from bodhan_mcp.schemas import (
    ChunkAudioOut,
    InspectOut,
    OcrDocOut,
    OcrPageOut,
    SpeakOut,
    TranscribeOut,
    TranslateOut,
    VoiceLibraryOut,
)
from bodhan_mcp.tools.media import chunk_audio as _chunk_audio
from bodhan_mcp.tools.media import get_voice_library as _get_voice_library
from bodhan_mcp.tools.media import inspect_media as _inspect_media
from bodhan_mcp.tools.transcribe import transcribe_audio as _transcribe_audio

# Ensure logger only writes to sys.stderr (Constraint C7)
logger = logging.getLogger("bodhan_mcp.server")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [bodhan_mcp.server] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

mcp = FastMCP("bodhan")


@mcp.tool()
async def transcribe_audio(
    input_path: str,
    language_hint: str | None = None,
) -> TranscribeOut:
    """Transcribe an audio file to text with global segment timestamps.

    What it does:
      Transcribes audio in Indian languages or English (best-effort).
      Returns full transcript, detected language, confidence, segments, duration, and INR cost.

    What it absorbs:
      Splits audio longer than 30s at natural pauses (target 25s, hard 30s) and remaps
      all chunk timestamps into a single monotonic global timeline.

    Supported languages:
      hi, ta, te, bn, mr, kn, ml, gu, pa, or, as, ur, en

    Errors returned:
      FILE_NOT_FOUND, UNREADABLE_MEDIA, UNSUPPORTED_LANGUAGE, UPSTREAM_*
    """
    res = await _transcribe_audio(input_path=input_path, language_hint=language_hint)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res


@mcp.tool()
def inspect_media(path: str) -> InspectOut:
    """Inspect local media file metadata cheaply without invoking any model.

    What it does:
      Extracts format, size, duration (for audio/video), and page count (for PDFs).

    What it does NOT do:
      Does not transcribe, OCR, or read text content.

    Cost: Free.

    Errors returned:
      FILE_NOT_FOUND
    """
    res = _inspect_media(path=path)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res


@mcp.tool()
def chunk_audio(path: str) -> ChunkAudioOut:
    """Slice an audio file at pauses into chunks <= 30s.

    What it does:
      Computes silence-bounded cut points and writes chunk WAV files.

    What it does NOT do:
      Does not transcribe.

    Cost: Free.

    Errors returned:
      FILE_NOT_FOUND, UNREADABLE_MEDIA
    """
    res = _chunk_audio(path=path)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res


@mcp.tool()
def get_voice_library() -> VoiceLibraryOut:
    """Discover available voices per supported Indic language for speech synthesis.

    Returns mapping of language code to list of available voice_id strings.
    English is NOT supported for speech synthesis.

    Cost: Free.
    """
    return _get_voice_library()


@mcp.resource("voices://library")
def voice_library_resource() -> str:
    """MCP Resource exposing the complete Indic voice library as JSON."""
    return json.dumps(config.VOICE_LIBRARY, indent=2)


# --- Forward-registered tools for complete surface ---

@mcp.tool()
async def translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
) -> TranslateOut:
    """Translate text preserving Markdown structure (headings, lists, tables).

    What it does:
      Translates text between supported Indic languages and English.

    What it absorbs:
      Paginates text exceeding 32K token context, respecting structural boundaries.
      Never splits inside tables or equations.

    Supported languages:
      hi, ta, te, bn, mr, kn, ml, gu, pa, or, as, ur, en

    Errors returned:
      UNSUPPORTED_LANGUAGE, INPUT_TOO_LONG, INVALID_ARGUMENT
    """
    from bodhan_mcp.tools.translate import translate_text as _translate_text
    res = await _translate_text(text=text, source_lang=source_lang, target_lang=target_lang)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res


@mcp.tool()
async def synthesize_speech(
    text: str,
    language: str,
    voice_id: str,
) -> SpeakOut:
    """Synthesize speech from Indic text using Indic-Speak.

    What it does:
      Generates audio from Indic text and stamps AI-generated metadata.

    What it does NOT do:
      Does NOT support English text directly (must translate to Indic first).
      Does NOT support voice cloning.

    Limits:
      5,000 character maximum per call.

    Errors returned:
      UNSUPPORTED_LANGUAGE, INPUT_TOO_LONG, INVALID_ARGUMENT
    """
    from bodhan_mcp.tools.speak import synthesize_speech as _synthesize_speech
    res = await _synthesize_speech(text=text, language=language, voice_id=voice_id)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res


@mcp.tool()
async def ocr_page(image_path: str) -> OcrPageOut:
    """Extract Markdown text and pipe tables from a single image.

    Errors returned:
      FILE_NOT_FOUND, UNREADABLE_MEDIA
    """
    from bodhan_mcp.tools.ocr import ocr_page as _ocr_page
    res = await _ocr_page(image_path=image_path)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res


@mcp.tool()
async def ocr_document(pdf_path: str) -> OcrDocOut:
    """Extract Markdown text from a multi-page PDF document.

    What it absorbs:
      Renders PDF pages at 200 DPI, runs OCR page by page, and assembles text in order.

    Errors returned:
      FILE_NOT_FOUND, UNREADABLE_MEDIA
    """
    from bodhan_mcp.tools.ocr import ocr_document as _ocr_document
    res = await _ocr_document(pdf_path=pdf_path)
    if isinstance(res, errors.ToolErrorPayload):
        raise ValueError(json.dumps(res.model_dump()))
    return res
