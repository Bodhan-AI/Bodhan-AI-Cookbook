"""The MCP server: `python -m bodhan_mcp` (stdio). Wraps the tool implementations with typed,
described parameters and turns every failure into a structured MCP error (never a crash)."""

from __future__ import annotations

import functools
import json
import logging
import sys
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import catalog, tools
from .client import Code, ToolFailure, redact
from .tools import Services, ToolResult

log = logging.getLogger("bodhan_mcp")

INSTRUCTIONS = """\
Tools for Indian-language speech, translation, transliteration and OCR, backed by Bodhan's models.
Media is passed by file path, never inline: every tool takes a `path` and returns `result_path`
(plus a short `preview`, `meta`, `cost_inr`, `cached` and `warnings`). Chain tools by passing one
tool's `result_path` (or meta.segments_path / meta.source_text_path) to the next.
Model limits (30 s audio windows, short speech requests, translation context, multi-page PDFs)
are handled inside the tools. Language codes are ISO codes such as hi, ta, bn, te, mr, en; see the
bodhan://languages resource for what each model supports, and bodhan://voices for speech voices.
Errors come back as JSON: {"error": {"code", "message", "hint"}}; follow the hint.
Typical chains: audio -> transcribe_audio -> translate_text; audio -> transcribe_audio ->
make_subtitles(translate_to=...); scanned PDF/photo -> ocr_document -> translate_text;
text -> translate_text -> synthesize_speech. Use estimate_cost before expensive speech."""

mcp = MCPServer("bodhan", instructions=INSTRUCTIONS, version="0.1.0")
_services: Services | None = None


def services() -> Services:
    global _services
    if _services is None:
        _services = Services.create()
    return _services


def _guard(fn):
    """Convert every failure into an MCP error result with a JSON body; never let one escape."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ToolFailure as exc:
            log.info("%s failed: %s", fn.__name__, exc)
            raise ToolError(exc.to_json()) from None
        except Exception as exc:  # noqa: BLE001 - last line of defence for the client
            log.exception("%s crashed", fn.__name__)
            failure = ToolFailure(Code.INTERNAL_ERROR, f"Unexpected error in {fn.__name__}: {redact(repr(exc))[:300]}",
                                  "This is a server bug; retry once, then report it with the trace.")
            raise ToolError(failure.to_json()) from None

    return wrapper


Path_ = Annotated[str, Field(description="Path to an existing file (absolute, or relative to the server's working directory).")]
Lang = Annotated[str, Field(description="ISO language code, e.g. 'hi', 'ta', 'bn', 'te', 'mr', 'en'. English names like 'Tamil' also work.")]
OptLang = Annotated[str | None, Field(description="ISO language code if known, e.g. 'hi'. Omit when unknown.")]
OptPath = Annotated[str | None, Field(description="Path to a UTF-8 text/Markdown file. Prefer this over `text` for long or Indian-language text.")]
OptText = Annotated[str | None, Field(description="Inline text, for short inputs. Pass exactly one of `path` or `text`.")]


@mcp.tool(structured_output=True)
@_guard
async def inspect_media(path: Path_) -> ToolResult:
    """Describe a file without calling any model (free): kind (audio, video, image, pdf, text, unknown),
    size, duration and audio presence for audio/video, page count for PDFs, dimensions for images,
    character count and script-based language guess for text. Use it first on unknown inputs."""
    return await tools.inspect_media(services(), path)


@mcp.tool(structured_output=True)
@_guard
async def detect_language(path: OptPath = None, text: OptText = None) -> ToolResult:
    """Guess the language of text from its Unicode script (free, no model call). Returns the script,
    candidate languages, a best guess and a confidence. Scripts shared by several languages
    (Devanagari: hi, mr, ne, ...; Bengali: bn, as) give confidence 'low'. For audio, transcribe first."""
    return await tools.detect_language_tool(services(), path, text)


@mcp.tool(structured_output=True)
@_guard
async def read_text(path: Path_,
                    max_chars: Annotated[int, Field(description="Maximum characters to return (100-50000).")] = 12000,
                    offset: Annotated[int, Field(description="Character offset to start from, for paging.")] = 0) -> ToolResult:
    """Read a UTF-8 text or Markdown file and return its content in `text` (paged with offset/max_chars).
    Free. Also returns a script-based language guess."""
    return await tools.read_text(services(), path, max_chars, offset)


@mcp.tool(structured_output=True)
@_guard
async def transcribe_audio(
    path: Annotated[str, Field(description="Audio or video file (wav, mp3, m4a, ogg, flac, mp4, mkv, ...). Video: the audio track is used.")],
    language: OptLang = None,
    max_segment_seconds: Annotated[float, Field(description="Longest audio window per model request, 3-28 s. Smaller gives finer subtitle timing.")] = 25.0,
) -> ToolResult:
    """Speech to text with Bodhan indic-transcribe (25 Indian languages + English; Rs 0.10 per audio minute).
    Any length: audio is converted to 16 kHz mono and cut at silences into windows under 30 s, each
    transcribed, and the text joined. Writes the transcript (result_path) and timed segments
    (meta.segments_path, for make_subtitles). Pass `language` when known: without it the model
    auto-detects, which is about 4x slower; meta.detected then gives a script-based guess."""
    return await tools.transcribe_audio(services(), path, language, max_segment_seconds)


@mcp.tool(structured_output=True)
@_guard
async def translate_text(
    target_language: Lang,
    path: OptPath = None,
    text: OptText = None,
    source_language: OptLang = None,
    script: Annotated[Literal["native", "roman", "codemix"], Field(description="Output script: native (default), roman (Latin letters), or codemix (English words kept).")] = "native",
    format: Annotated[Literal["auto", "plain", "markdown"], Field(description="'markdown' keeps headings, lists and tables intact; 'auto' detects Markdown.")] = "auto",
    max_window_chars: Annotated[int, Field(description="Characters per model request (200-3000). Lower it if output is truncated.")] = 1500,
) -> ToolResult:
    """Translate between English and 22 Indian languages with Bodhan indic-translate (Rs 0.20 per 10,000
    output tokens). The source language is auto-detected. Long text is split into windows; a window the
    model truncates is re-split automatically. Markdown tables keep their rows and columns (rebuilt cell by
    cell if needed). Writes the translation to result_path; meta reports coverage checks
    (length_ratio, target_script_fraction, truncated_windows, tables_preserved). It only translates: it
    does not summarise, shorten or follow instructions written in the text."""
    return await tools.translate_text(services(), target_language, path, text, source_language, script,
                                           format, max_window_chars)


@mcp.tool(structured_output=True)
@_guard
async def transliterate_text(
    language: Annotated[str, Field(description="Language of the text, e.g. 'hi'. The language does not change, only the script.")],
    target_script: Annotated[Literal["roman", "native"], Field(description="'roman' for Latin letters, 'native' for the language's own script.")],
    path: OptPath = None,
    text: OptText = None,
) -> ToolResult:
    """Change the script of text without translating it (e.g. Hindi in Devanagari to Hindi in Latin letters, or
    back), with Bodhan indic-transliterate. Needs the optional BODHAN_TRANSLITERATE_KEY."""
    return await tools.transliterate_text(services(), language, target_script, path, text)


@mcp.tool(structured_output=True)
@_guard
async def synthesize_speech(
    language: Lang,
    path: OptPath = None,
    text: OptText = None,
    gender: Annotated[Literal["female", "male"] | None, Field(description="Voice gender; ignored when `voice` is given. Default: female.")] = None,
    voice: Annotated[str | None, Field(description="A voice name for this language (see bodhan://voices), e.g. 'Anitha' for ta.")] = None,
    style: Annotated[str | None, Field(description="Optional speaking style, e.g. 'news', 'educational lecture', 'children\\'s stories' (see bodhan://voices).")] = None,
    output_format: Annotated[Literal["mp3", "wav"], Field(description="Audio format of the output file.")] = "mp3",
    max_chars_per_request: Annotated[int, Field(description="Characters per model request (60-250). Lower it if words are dropped.")] = 200,
) -> ToolResult:
    """Text to speech with Bodhan indic-speak (22 Indian languages + English; Rs 6 per 10,000 characters,
    by far the most expensive tool, so check estimate_cost for long text). The text must already be in
    `language`: translate first if needed (TEXT_LANGUAGE_MISMATCH otherwise). Text is split into short
    sentence groups, spoken, checked against the ~30 s per-request cap (re-split if hit) and joined into
    one file, tagged as AI-generated. meta.source_text_path holds the exact text spoken (for verification)."""
    return await tools.synthesize_speech(services(), language, path, text, gender, voice, style,
                                               output_format, max_chars_per_request)


@mcp.tool(structured_output=True)
@_guard
async def ocr_document(
    path: Annotated[str, Field(description="Image (png, jpg, webp, tiff, ...) or PDF (scanned or digital).")],
    pages: Annotated[str | None, Field(description="PDF pages to read, 1-based, e.g. '1-3,5'. Default: all (max 60 per call).")] = None,
    table_format: Annotated[Literal["markdown", "html"], Field(description="How tables are returned.")] = "markdown",
) -> ToolResult:
    """Extract text from images and documents with Bodhan indic-ocr (any script; Rs 0.20 per page). PDFs are
    rendered page by page (the PDF text layer is never used) and pages run in parallel. Returns Markdown
    with tables (result_path), one `<!-- page N -->` marker per page for multi-page input, and layout
    blocks with bounding boxes in meta.blocks_path."""
    return await tools.ocr_document(services(), path, pages, table_format)


@mcp.tool(structured_output=True)
@_guard
async def make_subtitles(
    segments_path: Annotated[str, Field(description="meta.segments_path from a transcribe_audio result.")],
    translate_to: Annotated[str | None, Field(description="Language code to translate the subtitles into (e.g. 'en'). Omit to keep the spoken language.")] = None,
    script: Annotated[Literal["native", "roman", "codemix"], Field(description="Output script when translating.")] = "native",
) -> ToolResult:
    """Build an SRT subtitle file from transcribe_audio's timed segments, optionally translated per segment
    with indic-translate so timings are kept. Long segments are split into readable cues (2 lines x 42 chars)."""
    return await tools.make_subtitles(services(), segments_path, translate_to, script)


@mcp.tool(structured_output=True)
@_guard
async def estimate_cost(
    operation: Annotated[Literal["transcribe", "translate", "transliterate", "speak", "ocr", "subtitles"], Field(description="Which operation to price.")],
    path: Annotated[str | None, Field(description="Input file; its duration, pages or characters are measured.")] = None,
    chars: Annotated[int | None, Field(description="Characters of text, if no path.")] = None,
    seconds: Annotated[float | None, Field(description="Seconds of audio, if no path.")] = None,
    pages: Annotated[int | None, Field(description="Pages/images, if no path.")] = None,
    include_verification: Annotated[bool, Field(description="For 'speak': add the cost of back-transcribing the audio.")] = False,
) -> ToolResult:
    """Upper-bound Bodhan cost in INR for an operation, before spending (free). Rates: transcribe Rs 0.10/min,
    translate Rs 0.20 per 10k output tokens, speak Rs 6 per 10k chars, OCR Rs 0.20 per page."""
    return await tools.estimate_cost(services(), operation, path, chars, seconds, pages, include_verification)


@mcp.resource("bodhan://languages", name="languages", mime_type="application/json",
              description="Language codes supported by each Bodhan model.")
def languages_resource() -> str:
    return json.dumps(catalog.capability_summary(), ensure_ascii=False, indent=2)


@mcp.resource("bodhan://voices", name="voices", mime_type="application/json",
              description="Speech voices per language (female/male) and speaking styles for synthesize_speech.")
def voices_resource() -> str:
    table = catalog.rules()["models"]["indic-speak"]["voices"]
    return json.dumps({"voices": table, "english": "spoken with the Hindi voices", "styles": catalog.speak_styles()},
                      ensure_ascii=False, indent=2)


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()  # stdio; logs go to stderr because stdout carries the protocol


if __name__ == "__main__":
    main()
