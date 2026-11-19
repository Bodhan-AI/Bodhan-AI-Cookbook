"""MCP registration only: six tools and three read-only resources over the same core code."""
from __future__ import annotations

import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .core import catalog, costs
from .core.errors import tool_guard
from .core.schemas import LanguageGuess, MediaInventory, OcrDocument, Speech, Transcript, Translation
from .tools.ocr import ocr_document
from .tools.probe import detect_language, probe_media
from .tools.speak import speak_text
from .tools.transcribe import transcribe_audio
from .tools.translate import translate_text

mcp = MCPServer(
    "bodhan",
    instructions=(
        "Bodhan Indic models as tools. Media goes in and out by file path: no tool takes or returns Indian-language "
        "text inline. Start with probe_media (free) to learn a file's kind and duration, and detect_language (free) "
        "for text files. Chunking, PDF rendering and model limits are handled inside each tool. Errors are JSON with "
        "code, message, retryable and hint."
    ),
)


FREE = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
PAID = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

OutDir = Annotated[str | None, Field(description="Folder for the output file. Defaults to .bodhan_out in the working directory. Created if missing.")]
MaxCost = Annotated[float | None, Field(description="Refuse before any request if the estimated cost in rupees exceeds this. Omit for no cap.")]
Force = Annotated[bool, Field(description="Ignore the result cache and pay for a fresh call. Leave false unless the cached output was wrong.")]


@mcp.tool(name="probe_media", title="Inspect media files", annotations=FREE)
@tool_guard
def probe_media_tool(paths: Annotated[list[str], Field(description="File or folder paths to inspect (a folder is read one level deep). Absolute or relative to the server's working directory.")]) -> MediaInventory:
    """Inspect audio, video, image, PDF and text files and report what they are, how long they are and how many pages they hold, so a caller can plan before paying for anything. Handles batches and directories (one level). Detects the script of text files and estimates transcription cost. Free: never calls a model."""
    return probe_media(paths)


@mcp.tool(name="detect_language", title="Detect language of a text file", annotations=FREE)
@tool_guard
def detect_language_tool(text_path: Annotated[str, Field(description="Path to a UTF-8 text file whose language you want identified.")]) -> LanguageGuess:
    """Identify the language of a text file from its writing system. Free and local. Returns a confident language only when the script is unambiguous; for Devanagari and other shared scripts it returns candidates and low confidence instead of guessing."""
    return detect_language(text_path)


@mcp.tool(name="transcribe_audio", title="Transcribe speech (Indic-Transcribe)", annotations=PAID)
@tool_guard
def transcribe_audio_tool(
    audio_path: Annotated[str, Field(description="Path to an audio file or a video with an audio track.")],
    language: Annotated[str | None, Field(description="Spoken language code, e.g. hi, ta, te. Omit to auto-detect (recommended when unsure). See bodhan://languages.")] = None,
    out_dir: OutDir = None,
    max_cost_inr: MaxCost = None,
    force: Force = False,
) -> Transcript:
    """Transcribe speech to a text file. Accepts WAV/FLAC/OGG/MP3, the containers the API refuses (M4A, AAC, WebM, AMR), and any video with an audio track. Long recordings are cut at natural pauses into windows of at most 30 seconds and merged, so any length works in one call. Returns the transcript path, per-window timestamps and a locally detected language. Omit `language` to let the model auto-detect."""
    return transcribe_audio(audio_path, language, out_dir, max_cost_inr, force)


@mcp.tool(name="ocr_document", title="Extract text from images and scans (Indic-OCR)", annotations=PAID)
@tool_guard
def ocr_document_tool(
    input_path: Annotated[str, Field(description="Path to an image (PNG, JPEG, WebP) or a PDF.")],
    pages: Annotated[str | None, Field(description='PDF pages to read, 1-based, e.g. "1-4" or "2,5". Omit for every page.')] = None,
    table_format: Annotated[str, Field(description="How tables are written: 'html' (keeps merged cells) or 'markdown'.")] = "html",
    dpi: Annotated[int, Field(description="Resolution used to render PDF pages. 300 suits print; raise only for tiny text.")] = 300,
    max_tokens: Annotated[int, Field(description="Starting output ceiling per page. The tool raises it itself if a page overflows.")] = 2048,
    out_dir: OutDir = None,
    max_cost_inr: MaxCost = None,
    force: Force = False,
) -> OcrDocument:
    """Extract text and tables from an image or a scanned multi-page PDF into a Markdown file. Pages are rendered and sent one at a time, so any page count works in one call. Headings are rebuilt from layout labels, page breaks marked with ---, tables kept as HTML (or Markdown). `pages` selects pages ("1-4", "2,5"). Returns Markdown and layout-JSON paths."""
    return ocr_document(input_path, pages, table_format, dpi, max_tokens, out_dir, max_cost_inr, force)


@mcp.tool(name="translate_text", title="Translate a text file (Indic-Translate)", annotations=PAID)
@tool_guard
def translate_text_tool(
    source_path: Annotated[str, Field(description="Path to a UTF-8 text or Markdown file. The source language is detected by the model; you do not pass it.")],
    target_language: Annotated[str, Field(description="Target language code such as hi, ta, mr, bn, en. Unsupported targets (e.g. fr) are refused before any request. See bodhan://languages.")],
    script: Annotated[str, Field(description="Script for languages written in more than one: 'native' (default), or a variant code like ks-Deva, sd-Arab, mni-Beng.")] = "native",
    out_dir: OutDir = None,
    max_cost_inr: MaxCost = None,
    force: Force = False,
) -> Translation:
    """Translate a UTF-8 text or Markdown file into one of Bodhan's 23 supported languages (26 codes: as, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur). The whole document is sent when it fits, preserving headings, lists and tables; longer documents split only at heading and page boundaries. Writes the translation to a file and returns its path. Rejects unsupported targets (e.g. French) before any request."""
    return translate_text(source_path, target_language, script, out_dir, max_cost_inr, force)


@mcp.tool(name="speak_text", title="Speak a text file (Indic-Speak)", annotations=PAID)
@tool_guard
def speak_text_tool(
    text_path: Annotated[str, Field(description="Path to a UTF-8 text file written in the language to speak.")],
    language: Annotated[str, Field(description="Language code of the text, e.g. ta, hi, bn. See bodhan://languages.")],
    voice: Annotated[str | None, Field(description="Exact voice name from bodhan://voices. Overrides gender.")] = None,
    gender: Annotated[str | None, Field(description="'female' or 'male'; picks that language's voice when no voice is given.")] = None,
    style: Annotated[str | None, Field(description="Speaking style from the fixed list in bodhan://voices. Omit for the default.")] = None,
    format: Annotated[str, Field(description="Output audio format: 'wav' or 'mp3'.")] = "wav",
    out_dir: OutDir = None,
    max_cost_inr: MaxCost = None,
    force: Force = False,
) -> Speech:
    """Speak a UTF-8 text file as a single audio file in one of Bodhan's 23 synthesis languages. Voice is optional (name, or gender female/male, or the language default); any voice can read any language. Long text is split into sentence groups, synthesised, checked for duration and joined, so any length works. Returns the audio path, duration and cost. Output is AI-generated (Indic-Speak)."""
    return speak_text(text_path, language, voice, gender, style, format, out_dir, max_cost_inr, force)


def _languages() -> str:
    rows = []
    for code in sorted(catalog.LANGUAGE_NAMES):
        rows.append(
            {
                "code": code,
                "name": catalog.LANGUAGE_NAMES[code],
                "asr": code in catalog.ASR_LANGUAGES,
                "translate": code in catalog.MT_LANGUAGES,
                "speak": code in catalog.TTS_LANGUAGES,
            }
        )
    return json.dumps({"languages": rows, "note": "Bare ISO codes; script variants use -Deva/-Beng/-Arab suffixes."}, ensure_ascii=False)


def _voices() -> str:
    rows = {lang: {"female": row.get("female", []), "male": row.get("male", []), "default": catalog.resolve_voice(lang, None, None)} for lang, row in catalog.VOICES.items()}
    rows["en"] = {"female": [catalog.ENGLISH_VOICE], "male": [catalog.ENGLISH_VOICE], "default": catalog.ENGLISH_VOICE}
    return json.dumps({"voices": rows, "styles": list(catalog.STYLES), "note": "Any voice can read any language; English has no voice of its own."}, ensure_ascii=False)


def _pricing() -> str:
    return json.dumps(
        {
            "prices_inr": {
                "asr": {"per_audio_minute": costs.ASR_PER_MIN},
                "translate": {"per_10k_output_tokens": costs.MT_PER_10K_TOKENS},
                "speak": {"per_10k_input_chars": costs.TTS_PER_10K_CHARS},
                "ocr": {"per_image": costs.OCR_PER_IMAGE},
            },
            "estimators": "asr: duration_s/60*price, rounded up to 0.01; translate: source_chars*1.2 tokens, settled from usage.completion_tokens; speak: len(text)/10000*price; ocr: pages*price",
        },
        ensure_ascii=False,
    )


@mcp.resource("bodhan://languages")
def languages_resource() -> str:
    """Every language code with its English name and which capabilities support it."""
    return _languages()


@mcp.resource("bodhan://voices")
def voices_resource() -> str:
    """Per-language voice names by gender, the default voice per language, and the fixed style list."""
    return _voices()


@mcp.resource("bodhan://pricing")
def pricing_resource() -> str:
    """The four model prices and how each tool estimates cost before a call."""
    return _pricing()


@mcp.prompt(description="Step-by-step recipe for turning a scanned document or photo into audio in another language.")
def document_to_audio(input_path: str, target_language: str) -> str:
    return (
        f"Make a spoken version of {input_path} in {target_language}.\n"
        f"1. probe_media on the file (free) to learn its kind and page count.\n"
        f"2. ocr_document, then translate_text to 'en' if you need to reason about it.\n"
        f"3. translate_text the final English text to '{target_language}'.\n"
        f"4. speak_text the translation. Read bodhan://voices first to choose a voice.\n"
        f"Every tool takes and returns file paths; never paste Indic text into your own messages."
    )


@mcp.prompt(description="Step-by-step recipe for subtitles from a voice recording.")
def audio_to_subtitles(audio_path: str, target_language: str = "en") -> str:
    return (
        f"Subtitle {audio_path} in {target_language}.\n"
        f"1. probe_media for the duration and cost estimate (bodhan://pricing explains the rates).\n"
        f"2. transcribe_audio without a language so the model detects it; keep the returned chunk timestamps.\n"
        f"3. translate_text the transcript to '{target_language}' unless the speech is already in it.\n"
        f"The API returns no word timings, so spread the text over each chunk's start and end."
    )
