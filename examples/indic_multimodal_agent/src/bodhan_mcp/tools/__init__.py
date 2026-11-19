"""Expose all Bodhan MCP tools."""

from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text, translate_subtitles_batch
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.ocr import run_ocr
from bodhan_mcp.tools.lang_detect import detect_text_language
from bodhan_mcp.tools.voices import list_voices, get_default_voice

__all__ = [
    "transcribe_audio",
    "translate_text",
    "translate_subtitles_batch",
    "synthesize_speech",
    "run_ocr",
    "detect_text_language",
    "list_voices",
    "get_default_voice",
]

