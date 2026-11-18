"""Expose the same four Bodhan-only tools over MCP stdio."""

from mcp.server.fastmcp import FastMCP

from . import bodhan_tools
from .config import Settings

mcp = FastMCP("bodhan-indic-tools")


def _settings() -> Settings:
    return Settings.from_env(require_llm=False)


@mcp.tool()
def indic_ocr(image_path: str, prompt: str = "Extract all visible text, preserving layout.") -> dict:
    """Extract text from a local document image with Bodhan Indic-OCR."""
    return bodhan_tools.indic_ocr(_settings(), image_path, prompt)


@mcp.tool()
def indic_asr(audio_path: str, language: str | None = None) -> dict:
    """Transcribe a local audio file with Bodhan Indic-Transcribe."""
    return bodhan_tools.indic_asr(_settings(), audio_path, language)


@mcp.tool()
def indic_translate(text: str, target_language: str, script: str = "native") -> dict:
    """Translate text into a supported Indic language or English using Bodhan."""
    return bodhan_tools.indic_translate(_settings(), text, target_language, script)


@mcp.tool()
def indic_tts(text: str, language: str, output_path: str, voice: str, style: str | None = None) -> dict:
    """Generate a local WAV file from text with Bodhan Indic-Speak."""
    return bodhan_tools.indic_tts(_settings(), text, language, output_path, voice, style)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
