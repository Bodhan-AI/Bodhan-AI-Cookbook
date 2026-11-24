"""
FastMCP server: exposes all Bodhan model capabilities as typed MCP tools.

Run standalone:  python -m bodhan_mcp
Any MCP client can discover and call these tools from their descriptions alone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from .tools.audio_chunk import chunk_audio
from .tools.constants import DEFAULT_VOICES, SUPPORTED_LANGUAGES
from .tools.language_detect import detect_language_from_text
from .tools.media_info import get_media_info
from .tools.ocr import ocr_file
from .tools.speak import synthesize_speech
from .tools.subtitles import create_srt_subtitles
from .tools.summarize import summarize_english
from .tools.transcribe import transcribe_audio
from .tools.translate import translate_text

mcp = FastMCP(
    "Bodhan Tools",
    instructions=(
        "Tools for Indic-language speech recognition, translation, text-to-speech, "
        "and OCR, powered by Bodhan's models. All file arguments must be absolute paths "
        "to local files. Never pass base64 data as a file path."
    ),
)


# ── transcribe ───────────────────────────────────────────────────────────────

@mcp.tool()
async def transcribe(
    audio_path: Annotated[str, "Absolute path to the audio file (any format ffmpeg supports)."],
    language: Annotated[
        str | None,
        "BCP-47 language hint (e.g. 'hi', 'ta', 'bn'). "
        "Omit to let Indic-Transcribe auto-detect the language.",
    ] = None,
) -> dict:
    """Transcribe speech from an audio file to text using Indic-Transcribe.

    Handles files of any length by automatically splitting into ≤28 s chunks
    and stitching the results. Returns the full transcript, the detected
    language, timestamped segments, and the cost in INR.

    Use this tool for: speech-to-text, language identification from audio,
    generating subtitle timecodes, and back-transcription of TTS output.
    """
    return await transcribe_audio(Path(audio_path), language=language)


# ── translate ────────────────────────────────────────────────────────────────

@mcp.tool()
async def translate(
    text: Annotated[str, "Source text to translate. Markdown, tables, and LaTeX are preserved."],
    source_lang: Annotated[str, "BCP-47 source language code (e.g. 'en', 'hi')."],
    target_lang: Annotated[
        str,
        f"BCP-47 target language code. Supported: {', '.join(SUPPORTED_LANGUAGES)}. "
        "Return an error message (not a translation) if the language is not in this list.",
    ],
) -> dict:
    """Translate text between languages using Indic-Translate.

    Handles long documents by splitting at paragraph boundaries. All Markdown,
    tables, LaTeX, numbers, and formatting are preserved in the output.

    Use this tool for: English ↔ Indian language translation, Indian ↔ Indian
    via English pivot, and generating back-translations for quality checks.
    Returns the translated text, source/target languages, and cost in INR.
    """
    if target_lang not in SUPPORTED_LANGUAGES and target_lang != "en":
        return {
            "error": f"Language '{target_lang}' is not supported. "
                     f"Supported languages: {', '.join(SUPPORTED_LANGUAGES)} and 'en'.",
            "translated_text": None,
            "cost_inr": 0.0,
        }
    return await translate_text(text, source_lang, target_lang)


# ── speak ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def speak(
    text: Annotated[str, "Text to synthesize. Must be in the target language already."],
    language: Annotated[str, f"BCP-47 language code. Supported: {', '.join(SUPPORTED_LANGUAGES)}."],
    out_dir: Annotated[str, "Absolute path to the directory where the MP3 will be written."],
    voice: Annotated[
        str | None,
        "Voice ID from the Indic-Speak library. "
        "Use 'female' or 'male' for a default voice in the given language, "
        "or a specific ID like 'hi-female-1'. Defaults to 'female'.",
    ] = None,
) -> dict:
    """Synthesize speech from text using Indic-Speak.

    Automatically chunks long text at sentence boundaries (≤500 chars per
    request) and stitches the audio. Returns the path to the output MP3,
    duration in seconds, cost in INR, and a warning if the duration looks
    suspicious (possible repetition or truncation by the model).

    Use this tool for: narration, audio summaries, and reading text aloud.
    Always label the output as AI-generated audio.
    """
    if language not in SUPPORTED_LANGUAGES:
        return {
            "error": f"Language '{language}' is not supported for speech synthesis. "
                     f"Supported: {', '.join(SUPPORTED_LANGUAGES)}.",
            "audio_path": None,
            "cost_inr": 0.0,
        }
    resolved_voice = _resolve_voice(language, voice)
    return await synthesize_speech(text, language, resolved_voice, Path(out_dir))


def _resolve_voice(language: str, voice: str | None) -> str:
    defaults = DEFAULT_VOICES.get(language, {})
    if voice is None or voice.lower() in ("female", "f"):
        return defaults.get("female", "Kavya")
    if voice.lower() in ("male", "m"):
        return defaults.get("male", "Amit")
    return voice


# ── ocr ──────────────────────────────────────────────────────────────────────

@mcp.tool()
async def ocr(
    file_path: Annotated[
        str,
        "Absolute path to an image file (PNG, JPG, WEBP) or a scanned PDF. "
        "Do NOT use the PDF's embedded text layer - render pages as images first.",
    ],
) -> dict:
    """Extract text from a scanned image or PDF using Indic-OCR.

    PDFs are rendered page-by-page at 200 DPI and each page is OCR'd
    concurrently. Returns all pages with their text, a confidence score,
    and a 'flagged' marker for pages with low confidence that should not be
    translated without review. Also returns the full concatenated text and
    cost in INR.

    Use this tool for: extracting text from scanned documents, worksheets,
    textbook pages, and photographs of printed text.
    """
    return await ocr_file(Path(file_path))


# ── language_detect ──────────────────────────────────────────────────────────

@mcp.tool()
def language_detect(
    text: Annotated[str, "Text whose language should be detected."],
) -> dict:
    """Detect the language of a text string from its Unicode script.

    Returns the dominant BCP-47 language code, the Unicode script name, and
    a confidence score (0-1). Works offline with no API call. For audio
    language detection, use the transcribe tool instead (it returns the
    detected language in its response).

    Returns 'en' for Latin-script text. Returns 'unknown' if the script
    cannot be identified.
    """
    return detect_language_from_text(text)


# ── audio_chunk ──────────────────────────────────────────────────────────────

@mcp.tool()
def audio_chunk(
    audio_path: Annotated[str, "Absolute path to the audio file to split."],
    max_seconds: Annotated[
        float,
        "Maximum duration of each chunk in seconds. "
        "Default is 28 (under Indic-Transcribe's 30 s limit).",
    ] = 28.0,
) -> dict:
    """Split an audio file into chunks at silence boundaries.

    Use this tool when you need fine-grained control over chunking (e.g., to
    align chunks with speaker turns). The transcribe tool handles chunking
    automatically, so you only need this tool if you want the chunk paths
    for other purposes.

    Returns a list of absolute paths to the chunk files. If the original file
    is already short enough, returns [audio_path] unchanged.
    """
    paths = chunk_audio(Path(audio_path), max_seconds=max_seconds)
    return {"chunks": [str(p) for p in paths], "count": len(paths)}


# ── summarize ────────────────────────────────────────────────────────────────

@mcp.tool()
async def summarize(
    text: Annotated[str, "English text to summarize."],
    target_duration_seconds: Annotated[
        int,
        "Target length of the spoken summary in seconds (e.g. 120 for a 2-minute summary).",
    ] = 120,
) -> dict:
    """Summarize English text into a concise spoken summary using the LLM brain.

    Use this tool to create summaries of chapters or documents before translating
    and narrating them with Bodhan models.
    """
    return await summarize_english(text, target_duration_seconds=target_duration_seconds)


# ── subtitles ────────────────────────────────────────────────────────────────

@mcp.tool()
def subtitles(
    text: Annotated[str, "Transcript or subtitle text."],
    out_path: Annotated[str, "Absolute path where the .srt file will be saved."],
    segments: Annotated[list[dict] | None, "Optional list of timestamped segments."] = None,
) -> dict:
    """Format and write an SRT subtitle file from transcription or translation output."""
    return create_srt_subtitles(text, segments, Path(out_path))


# ── media_info ───────────────────────────────────────────────────────────────

@mcp.tool()
def media_info(
    file_path: Annotated[str, "Absolute path to the file to inspect."],
) -> dict:
    """Inspect a media file to determine its type, duration, format, and size.

    Use this tool to understand what kind of file you are dealing with before
    choosing which Bodhan tool to call. Returns type ('audio', 'video',
    'image', 'pdf', 'text'), format name, duration in seconds (None for
    non-media files), and size in bytes.
    """
    return get_media_info(Path(file_path))
