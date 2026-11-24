from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools import chunk_audio, inspect_media, ocr_image, render_pdf_pages, synthesize_speech, transcribe_audio, translate_text

mcp = FastMCP("Bodhan Indic Models")

@mcp.tool()
def bodhan_inspect_media(file: str) -> dict:
    """Inspect a local file and report whether it is text, image, PDF, audio, or video. Pass a local file reference, never base64."""
    return inspect_media(file)

@mcp.tool()
def bodhan_render_pdf_pages(file: str, output_dir: str, dpi: int = 150) -> dict:
    """Render a local PDF to PNG pages for Bodhan OCR. It does not run OCR and can handle multi-page PDFs."""
    return render_pdf_pages(file, output_dir, dpi)

@mcp.tool()
def bodhan_chunk_audio(file: str, output_dir: str, chunk_seconds: int = 30) -> dict:
    """Normalize audio to 16 kHz mono WAV and split it into at most 30-second chunks. Returns local file references."""
    return chunk_audio(file, output_dir, chunk_seconds)

@mcp.tool()
async def bodhan_transcribe_audio(file: str, language: str | None = None) -> dict:
    """Transcribe at most 30 seconds of WAV/MP3/FLAC/OGG with Indic-Transcribe. Use a 25-language ISO code such as hi, ta or te when known; omit it to auto-detect."""
    return await transcribe_audio(file, language)

@mcp.tool()
async def bodhan_translate_text(text: str, target_language: str, script: str = "native", max_output_tokens: int | None = None) -> dict:
    """Translate text with Indic-Translate to an Indic or English target code. Native script is the default. Set script=roman or codemix only when requested. Large text should be split into paragraphs; truncated=true means incomplete."""
    return await translate_text(text, target_language, script, max_output_tokens)

@mcp.tool()
async def bodhan_synthesize_speech(text: str, language: str, voice: str, style: str | None = None) -> dict:
    """Create WAV speech with Indic-Speak. Use a short sentence or two; text longer than about 30 seconds should be chunked by the caller. Returns AI-generated labeled audio and a local file reference."""
    return await synthesize_speech(text, language, voice, style)

@mcp.tool()
async def bodhan_ocr_image(file: str, table_format: str = "markdown", max_tokens: int = 2048) -> dict:
    """Extract reading-ordered text and layout from one PNG/JPEG using Indic-OCR. Use markdown for tables or html for merged/dense tables. max_tokens applies per block."""
    return await ocr_image(file, table_format, max_tokens)


def main() -> None:
    mcp.run(transport="stdio")
