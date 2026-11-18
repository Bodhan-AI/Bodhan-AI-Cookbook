#!/usr/bin/env python3
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
import json

from .tools import (
    inspect_media,
    transcribe_audio,
    extract_document_text,
    translate_text,
    synthesize_speech,
)
from .models import (
    SUPPORTED_LANGUAGES,
    SUPPORTED_TRANSCRIBE_LANGUAGES,
    SUPPORTED_TRANSLATE_LANGUAGES,
    SUPPORTED_TTS_LANGUAGES,
    SUPPORTED_OCR_LANGUAGES,
    TTS_VOICES,
    TTS_STYLES,
    TRANSLATE_SCRIPTS,
    TRANSLITERATE_SCRIPTS,
    OCR_TABLE_FORMATS,
    COST_PER_MINUTE_STT,
    COST_PER_10K_OUTPUT_TOKENS_TRANSLATE,
    COST_PER_10K_CHARS_SPEAK,
    COST_PER_IMAGE_OCR,
)


mcp = FastMCP(name="bodhan-mcp")


CAPABILITIES = {
    "supported_languages": sorted(list(SUPPORTED_LANGUAGES)),
    "transcribe_languages": sorted(list(SUPPORTED_TRANSCRIBE_LANGUAGES)),
    "translate_languages": sorted(list(SUPPORTED_TRANSLATE_LANGUAGES)),
    "tts_languages": sorted(list(SUPPORTED_TTS_LANGUAGES)),
    "ocr_languages": sorted(list(SUPPORTED_OCR_LANGUAGES)),
    "tts_voices": {lang: voices for lang, voices in TTS_VOICES.items()},
    "tts_styles": sorted(list(TTS_STYLES)),
    "translate_scripts": sorted(list(TRANSLATE_SCRIPTS)),
    "transliterate_scripts": sorted(list(TRANSLITERATE_SCRIPTS)),
    "ocr_table_formats": sorted(list(OCR_TABLE_FORMATS)),
    "pricing_inr": {
        "transcribe_per_minute": COST_PER_MINUTE_STT,
        "translate_per_10k_output_tokens": COST_PER_10K_OUTPUT_TOKENS_TRANSLATE,
        "speak_per_10k_input_chars": COST_PER_10K_CHARS_SPEAK,
        "ocr_per_image": COST_PER_IMAGE_OCR,
    },
    "models": {
        "transcribe": "indic-transcribe",
        "translate": "indic-translate",
        "speak": "indic-speak",
        "ocr": "indic-ocr",
        "transliterate": "indic-transliterate",
    },
}


@mcp.resource("bodhan://capabilities")
async def get_capabilities() -> str:
    return json.dumps(CAPABILITIES, indent=2)


@mcp.tool(
    name="inspect_media",
    description=(
        "Inspect a media file (audio, video, PDF, image, or text) and return "
        "metadata including format, duration, dimensions, page count, and minimum "
        "INR cost estimate for processing with Bodhan APIs. Does not call any paid API."
    ),
)
async def tool_inspect_media(file_path: str) -> dict:
    return await inspect_media(file_path)


@mcp.tool(
    name="transcribe_audio",
    description=(
        "Transcribe audio/video file to text using Bodhan Indic-Transcribe. "
        "Auto extracts audio, converts to 16kHz mono WAV, splits into <=30s chunks "
        "at silence boundaries, stitches results with global timestamps. "
        "Optional max_duration_sec for cheap language probes (e.g., 15s). "
        "Returns wrapped text in <untrusted_source_data> tags for prompt "
        "injection safety."
    ),
)
async def tool_transcribe_audio(
    file_path: str,
    language_hint: str | None = None,
    max_duration_sec: float | None = None,
) -> dict:
    return await transcribe_audio(file_path, language_hint, max_duration_sec)


@mcp.tool(
    name="extract_document_text",
    description=(
        "Extract text from images or multi-page PDFs using Bodhan Indic-OCR. "
        "Renders PDF pages to PNGs, processes with bounded concurrency (3 parallel), "
        "returns page-indexed Markdown with layout blocks (bbox, confidence, type). "
        "Optional page_range like '1-3' or '5'. Returns wrapped text in "
        "<untrusted_source_data> tags for prompt injection safety."
    ),
)
async def tool_extract_document_text(
    file_path: str,
    page_range: str | None = None,
) -> dict:
    return await extract_document_text(file_path, page_range)


@mcp.tool(
    name="translate_text",
    description=(
        "Translate text between Indian languages using Bodhan Indic-Translate. "
        "Validates source/target languages upfront (returns structured "
        "UNSUPPORTED_LANGUAGE error for unsupported languages like French). "
        "Preserves Markdown tables and paragraph boundaries during chunking. "
        "Supports output scripts: native, roman, codemix. Optionally writes "
        "result to output_file. Returns target_script_verified boolean checking "
        ">=85% target Unicode script ratio on letters."
    ),
)
async def tool_translate_text(
    source_lang: str,
    target_lang: str,
    text: str | None = None,
    input_file: str | None = None,
    output_file: str | None = None,
    script: str = "native",
) -> dict:
    return await translate_text(
        source_lang, target_lang, text, input_file, output_file, script
    )


@mcp.tool(
    name="synthesize_speech",
    description=(
        "Synthesize speech from text using Bodhan Indic-Speak. "
        "Validates language and voice, splits text on sentence boundaries "
        "(।, ., ?, !) within 3000-char chunks, calls Indic-Speak per chunk, "
        "stitches audio via ffmpeg concat, embeds AI-generated metadata tag. "
        "Supports 45 voices across 22 languages and 13 speaking styles. "
        "Returns output_file path, duration, chunk count, and INR cost."
    ),
)
async def tool_synthesize_speech(
    language: str,
    output_file: str,
    text: str | None = None,
    input_file: str | None = None,
    gender: str = "female",
    voice_id: str | None = None,
    style: str | None = None,
) -> dict:
    return await synthesize_speech(
        language, output_file, text, input_file, gender, voice_id, style
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
