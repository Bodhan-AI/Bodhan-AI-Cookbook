"""Bodhan MCP server (stdio): exposes the six tools over MCP.

Run it on its own with ``python -m bodhan_mcp``; any MCP client can then
list and call the tools. The server wraps the same tool functions the
task agent imports directly, so behaviour is identical in both paths.

Environment: Bodhan keys come from BODHAN_*_KEY (see .env.example).
BODHAN_MOCK=1 switches the server to the mock client (local testing
only — canned responses, no network, no cost).
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from . import languages, tools
from .client import get_client
from .dotenv import load_dotenv
from .errors import ToolError


def create_server(mock: bool | None = None) -> FastMCP:
    client = get_client(mock=mock)
    mcp = FastMCP("bodhan-tool-server")

    @mcp.tool()
    def transcribe_audio(
        file: str,
        language: str | None = None,
        model: str = "core",
    ) -> dict:
        """Transcribe an audio file to text with Bodhan Indic-Transcribe.

        Give the file path; long audio is split into <=30s chunks
        automatically. Pass language (ISO code) when known, omit it for
        auto-detect. Returns the transcript, language, per-chunk segments
        with timestamps, duration and cost in INR.
        """
        try:
            return tools.transcribe_audio(client, file, language=language, model=model)
        except ToolError as exc:
            return {"error": exc.to_dict()}

    @mcp.tool()
    def translate_text(
        text: str,
        target_language: str,
        script: str = "native",
    ) -> dict:
        """Translate text into a target language with Bodhan Indic-Translate.

        The source language is inferred; only the target is named. Long
        text is split at sentence boundaries. Supported targets are the 23
        Indic languages plus English; French, German and other European
        languages are NOT supported and fail with unsupported_language.
        Returns the translation and cost in INR.
        """
        try:
            return tools.translate_text(client, text, target_language, script=script)
        except ToolError as exc:
            return {"error": exc.to_dict()}

    @mcp.tool()
    def synthesize_speech(
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
        out_path: str | None = None,
    ) -> dict:
        """Read text aloud with Bodhan Indic-Speak and write a WAV file.

        Give the text, the language ISO code and optionally a voice name
        (default: the language's female voice) and a speaking style. Long
        text is split into short chunks and joined. Returns the audio file
        path, duration, voice and cost in INR. The audio is AI-generated;
        label it as such when sharing.
        """
        try:
            return tools.synthesize_speech(
                client, text, language, voice=voice, style=style, out_path=out_path
            )
        except ToolError as exc:
            return {"error": exc.to_dict()}

    @mcp.tool()
    def ocr_document(
        file: str,
        table_format: str = "markdown",
    ) -> dict:
        """Extract reading-ordered text from a page image (PNG/JPEG) or a PDF
        with Bodhan Indic-OCR. PDFs are rendered to page images first, one
        OCR call per page. Returns Markdown, per-page text, block count and
        cost in INR.
        """
        try:
            return tools.ocr_document(client, file, table_format=table_format)
        except ToolError as exc:
            return {"error": exc.to_dict()}

    @mcp.tool()
    def inspect_media(file: str) -> dict:
        """Describe a file for planning: kind (audio/image/document/text/
        video), format, size, audio duration, image dimensions or PDF page
        count. Local only, no model call, no cost. Call it on every input
        file before choosing other tools.
        """
        try:
            return tools.inspect_media(file)
        except ToolError as exc:
            return {"error": exc.to_dict()}

    @mcp.tool()
    def identify_language(
        text: str | None = None,
        audio_file: str | None = None,
    ) -> dict:
        """Identify the language of text or of an audio clip. For text this
        is deterministic script analysis (no model call) and returns the
        script plus candidate languages; when several languages share a
        script the answer is ambiguous and says so. For audio the clip is
        transcribed first, then analysed.
        """
        try:
            return tools.identify_language(client, text=text, audio_file=audio_file)
        except ToolError as exc:
            return {"error": exc.to_dict()}

    @mcp.resource("bodhan://voices")
    def voices_resource() -> str:
        """The Indic-Speak voice library: 45 voices by language and gender.

        Any voice can read any language; the table groups each voice by
        the language it was recorded in. Use a voice name in
        synthesize_speech.
        """
        return json.dumps(
            {
                "note": "Any voice can read any language. Default: the language's female voice.",
                "voices": languages.VOICES,
                "styles": sorted(languages.TTS_STYLES),
            },
            indent=2,
        )

    return mcp


def main() -> None:
    load_dotenv()  # .env in the recipe dir (keys never printed, never committed)
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
