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


@mcp.resource("bodhan://voices")
async def get_voices() -> str:
    return json.dumps(TTS_VOICES, indent=2)


@mcp.tool(
    name="inspect_media",
    description=(
        "Inspect a media file (audio, video, PDF, image, or text) and return "
        "metadata including format, duration, dimensions, page count, and minimum "
        "INR cost estimate for processing with Bodhan APIs.\n"
        "Does not call any paid API.\n\n"
        "Supported file extensions: .wav .mp3 .m4a .flac .ogg .mp4 .mov .avi .mkv "
        "(audio/video); .pdf (PDF); .png .jpg .jpeg .webp .bmp .tiff (images); "
        ".txt .md (text).\n\n"
        "Returns: format, duration_sec, pages (PDF), dimensions (image), "
        "min_cost_inr, supported_for_transcribe/translate/ocr/tts booleans."
    ),
)
async def tool_inspect_media(file_path: str) -> dict:
    return await inspect_media(file_path)


@mcp.tool(
    name="transcribe_audio",
    description=(
        "Transcribe audio/video file to text using Bodhan Indic-Transcribe ("
        "indic-transcribe-core / indic-transcribe-flex). Auto extracts audio, "
        "converts to 16kHz mono WAV, splits into <=30s chunks at silence boundaries, "
        "stitches results with global timestamps.\n\n"
        "Language support (accepts ISO code or English name): hi/Hindi, ta/Tamil, "
        "te/Telugu, mr/Marathi, bn/Bengali, en/English, gu/Gujarati, kn/Kannada, "
        "ml/Malayalam, pa/Punjabi, or/Odia, as/Assamese, mai/Maithili, bho/Bhojpuri, "
        "sat/Santali, bhili/Bhili, brx/Bodo, doi/Dogri, kok/Konkani, ks/Kashmiri, "
        "sd/Sindhi, sa/Sanskrit, ur/Urdu, mni/Manipuri, ne/Nepali.\n\n"
        "Parameters:\n"
        "  - file_path (str, required): Path to audio/video file.\n"
        "  - language_hint (str | None, optional): ISO code or English name of source "
        "language (e.g. 'hi' or 'Hindi'). Pass 'en' for English probe.\n"
        "  - max_duration_sec (float | None, optional): Cheap language probe by "
        "limiting processing to first N seconds (e.g. 15.0). Must be > 0.\n\n"
        "Internal limits: audio auto-chunked at <=30s boundaries. File references only—"
        "no base64 input.\n\n"
        "Returns: text preview (~2500 chars) wrapped in <untrusted_source_data> tags, "
        "full_text_file path to complete transcript, segments with timestamps, "
        "cost_inr."
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
        "Extract text from images or multi-page PDFs using Bodhan Indic-OCR "
        "(indic-ocr). Renders PDF pages to PNGs via pypdfium2, processes with bounded "
        "concurrency (3 parallel requests), returns page-indexed Markdown with layout "
        "blocks (bbox, confidence, type).\n\n"
        "Supported file extensions: .pdf .png .jpg .jpeg .webp .bmp .tiff .txt .md\n\n"
        "Parameters:\n"
        "  - file_path (str, required): Path to document file.\n"
        "  - page_range (str | None, optional): Pages to process, e.g. '1-3' or '5'. "
        "1-indexed; '0' or negative values error. Start must be <= end.\n\n"
        "Internal limits: multi-page PDF rendered with concurrency cap of 3. File "
        "references only—no base64 sent to caller (API uses base64 internally).\n\n"
        "Returns: markdown preview (~2500 chars) wrapped in <untrusted_source_data> "
        "tags, text key with full extracted text, full_text_file path, blocks array, "
        "cost_inr."
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
        "Translate text between Indian languages using Bodhan Indic-Translate "
        "(indic-translate).\n\n"
        "Language support (both source and target accept ISO code or English name): "
        "hi/Hindi, ta/Tamil, te/Telugu, mr/Marathi, bn/Bengali, en/English, gu/"
        "Gujarati, kn/Kannada, ml/Malayalam, pa/Punjabi, or/Odia, as/Assamese, "
        "mai/Maithili, bho/Bhojpuri, sat/Santali, bhili/Bhili, brx/Bodo, doi/Dogri, "
        "kok/Konkani, ks/Kashmiri, sd/Sindhi, sa/Sanskrit, ur/Urdu, mni/Manipuri, "
        "ne/Nepali.\n\n"
        "Parameters:\n"
        "  - source_lang (str, required): Source language ISO code or English name.\n"
        "  - target_lang (str, required): Target language ISO code or English name.\n"
        "  - text (str | None, optional): Raw text to translate. Mutually exclusive "
        "with input_file; provide one.\n"
        "  - input_file (str | None, optional): Path to .txt or .md file to read text "
        "from. Mutually exclusive with text; provide one.\n"
        "  - output_file (str | None, optional): Path to write translated text. "
        "Optional.\n"
        "  - script (str, default='native'): Output script — 'native', 'roman', or "
        "'codemix'.\n\n"
        "Internal limits: long text chunked by paragraph/table boundaries for context "
        "window. File references only—no base64 input.\n\n"
        "Pricing: ₹0.20 per 10,000 output tokens.\n\n"
        "Returns: translation wrapped in <untrusted_source_data> tags, "
        "target_script_verified (>=85% target Unicode script ratio), cost_inr, "
        "output_file path if written."
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
        "Synthesize speech from text using Bodhan Indic-Speak (indic-speak). "
        "Validates language and voice, splits text on sentence boundaries (।, ., ?, !) "
        "within 3000-char chunks, calls Indic-Speak per chunk, stitches audio via "
        "ffmpeg concat, embeds AI-generated metadata tag.\n\n"
        "Language support (accepts ISO code or English name): hi/Hindi, ta/Tamil, "
        "te/Telugu, mr/Marathi, bn/Bengali, en/English, gu/Gujarati, kn/Kannada, "
        "ml/Malayalam, pa/Punjabi, or/Odia, as/Assamese, mai/Maithili, bho/Bhojpuri, "
        "sat/Santali, bhili/Bhili, brx/Bodo, doi/Dogri, kok/Konkani, ks/Kashmiri, "
        "sd/Sindhi, sa/Sanskrit, ur/Urdu.\n\n"
        "Voice catalog at bodhan://voices resource. Valid genders: female, male.\n\n"
        "Parameters:\n"
        "  - language (str, required): Target language ISO code or English name.\n"
        "  - output_file (str, required): Path to write output WAV file.\n"
        "  - text (str | None, optional): Raw text to synthesize. Mutually exclusive "
        "with input_file; provide one.\n"
        "  - input_file (str | None, optional): Path to .txt or .md file to read text "
        "from. Mutually exclusive with text; provide one.\n"
        "  - gender (str, default='female'): Voice gender — 'female' or 'male'.\n"
        "  - voice_id (str | None, optional): Specific voice ID from bodhan://voices. "
        "If omitted, uses default female voice for language.\n"
        "  - style (str | None, optional): Speaking style from TTS_STYLES catalog.\n\n"
        "Internal limits: text split at sentence boundaries within 3000-char chunks.\n"
        "Pricing: ₹6.00 per 10,000 input characters.\n\n"
        "Returns: output_file path, duration_sec, chunks_count, cost_inr."
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


@mcp.prompt()
def document_to_indic_audio() -> str:
    return (
        "You have a document (PDF/image/text) in an Indian language. "
        "Workflow: (1) inspect_media to understand the file, "
        "(2) extract_document_text for OCR if needed, "
        "(3) translate_text to target Indic language if needed, "
        "(4) synthesize_speech to produce audio. "
        "Always wrap untrusted OCR/transcript text in <untrusted_source_data> tags. "
        "Use max_duration_sec=15 probe for unknown audio languages."
    )


@mcp.prompt()
def media_to_english_subtitles() -> str:
    return (
        "You have audio/video media. Workflow: "
        "(1) inspect_media, (2) transcribe_audio (probe with "
        "max_duration_sec=15 if language unstated), "
        "(3) translate_text from detected Indic language to English, "
        "(4) format_srt from verifier to produce .srt subtitle file. "
        "Return English subtitles preserving timing information."
    )


@mcp.prompt()
def preserve_table_translation() -> str:
    return (
        "You have a document containing Markdown tables that must be translated "
        "while preserving table structure. Workflow: "
        "(1) extract_document_text to get the content, "
        "(2) translate_text — the Bodhan translate tool preserves Markdown table "
        "structure via chunking; verify table_parity afterward. "
        "Do NOT edit table pipes or column counts yourself."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
