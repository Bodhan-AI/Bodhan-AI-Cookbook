"""Standalone FastMCP server exposing Bodhan Indic AI models over stdio."""

from __future__ import annotations

import json
from typing import Any
from mcp.server.fastmcp import FastMCP

from bodhan_mcp.tools import BodhanTools

mcp = FastMCP(
    name="bodhan-indic-server",
    instructions=(
        "MCP Server providing access to Bodhan's Indic AI models: "
        "Indic-Transcribe (ASR), Indic-Translate (Translation), "
        "Indic-Speak (TTS), and Indic-OCR (Document OCR). "
        "All media must be referenced by local file paths. "
        "All model limits (audio chunking, TTS length, PDF pages) are handled automatically."
    ),
)

# Initialize tool backend
tools_backend = BodhanTools()


@mcp.tool(
    name="transcribe_audio",
    description=(
        "Transcribe speech from an audio file into text using Bodhan Indic-Transcribe. "
        "Accepts any audio file format (WAV, MP3, M4A, etc.) via local file_path. "
        "Long files (>30s) are automatically chunked into <=30s windows and reassembled. "
        "Returns full transcript, per-window results with timestamps, and quality flags."
    ),
)
def transcribe_audio(file_path: str, language: str = "") -> str:
    """Transcribes audio file to text."""
    res = tools_backend.transcribe_audio(file_path, language=language if language else None)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="translate_text",
    description=(
        "Translate text between English and 22 official Indian languages using Indic-Translate. "
        "Accepts either text directly or a file_path to translate. "
        "Preserves document layout, blank lines, and formatting across paragraphs. "
        "Unsupported non-Indian languages (e.g. French, German) fail with structured error."
    ),
)
def translate_text(text: str = "", file_path: str = "", target_language_code: str = "hi", target_script: str = "native") -> str:
    """Translates text or file to target language."""
    res = tools_backend.translate_text(
        text=text,
        file_path=file_path,
        target_language_code=target_language_code,
        target_script=target_script,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="synthesize_speech",
    description=(
        "Generate natural speech audio from text using Indic-Speak. "
        "Automatically splits sentences into <= 150 character chunks, synthesizes each, "
        "and joins them into a single WAV audio file on disk. Returns audio_path and duration."
    ),
)
def synthesize_speech(
    text: str,
    language: str,
    voice: str = "",
    style: str = "",
    output_path: str = "",
) -> str:
    """Synthesizes text to audio WAV file."""
    res = tools_backend.synthesize_speech(
        text=text,
        language=language,
        voice=voice if voice else None,
        style=style if style else None,
        output_path=output_path if output_path else None,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="extract_document_text",
    description=(
        "Extract text from images (PNG, JPG) or multi-page PDF documents using Indic-OCR. "
        "Renders multi-page PDFs to high-resolution images, executes OCR per page, "
        "and returns clean Markdown with page delimiters and layout blocks."
    ),
)
def extract_document_text(file_path: str) -> str:
    """Extracts text from image or PDF."""
    res = tools_backend.extract_document_text(file_path)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="inspect_media",
    description=(
        "Free inspection tool for media files (audio, video, PDF, images, text). "
        "Returns format, duration in seconds, PDF page count, dimensions, and estimated "
        "Bodhan API costs before performing any paid operations."
    ),
)
def inspect_media(file_path: str) -> str:
    """Inspects media file properties and estimated cost."""
    res = tools_backend.inspect_media(file_path)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="detect_language",
    description=(
        "Detect the language and script of text (via Unicode analysis) or audio (via short ASR). "
        "Returns ISO language code (e.g. 'hi', 'ta', 'te') and English language name."
    ),
)
def detect_language(text: str = "", audio_path: str = "") -> str:
    """Detects language of text or audio."""
    res = tools_backend.detect_language(
        text=text if text else None,
        audio_path=audio_path if audio_path else None,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="extract_audio_from_video",
    description=(
        "Extracts an audio track from a video file (MP4, MKV, MOV, etc.) and converts "
        "it to a 16 kHz mono WAV file suitable for Indic-Transcribe."
    ),
)
def extract_audio_from_video(video_path: str, output_path: str = "") -> str:
    """Extracts audio from video."""
    res = tools_backend.extract_audio_from_video(
        video_path=video_path,
        output_path=output_path if output_path else None,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool(
    name="build_srt",
    description=(
        "Converts timestamped transcript segments into standard SubRip (.srt) subtitle format. "
        "Accepts JSON string of segments with start_s, end_s, text. Writes .srt file if output_path given."
    ),
)
def build_srt(segments_json: str, output_path: str = "") -> str:
    """Builds SRT subtitle content from segments."""
    try:
        segments = json.loads(segments_json)
        if not isinstance(segments, list):
            return json.dumps({"success": False, "error": "segments_json must be a JSON array of segment objects."})
    except json.JSONDecodeError as e:
        return json.dumps({"success": False, "error": f"Invalid JSON for segments: {str(e)}"})

    res = tools_backend.build_srt(segments, output_path=output_path if output_path else None)
    return json.dumps(res, ensure_ascii=False, indent=2)


def run_server() -> None:
    """Runs the FastMCP server over stdio."""
    mcp.run(transport="stdio")
