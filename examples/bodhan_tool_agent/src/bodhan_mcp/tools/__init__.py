"""Bodhan MCP tools package."""

from .media_helpers import (
    bodhan_detect_language,
    bodhan_inspect_media,
    bodhan_list_voices,
    bodhan_read_text,
)
from .ocr import bodhan_ocr
from .speak import bodhan_speak
from .transcribe import bodhan_transcribe
from .translate import bodhan_translate
from .verify_and_subtitles import (
    bodhan_make_subtitles,
    bodhan_verify_speech,
    bodhan_verify_translation,
)

__all__ = [
    "bodhan_detect_language",
    "bodhan_inspect_media",
    "bodhan_list_voices",
    "bodhan_make_subtitles",
    "bodhan_ocr",
    "bodhan_read_text",
    "bodhan_speak",
    "bodhan_transcribe",
    "bodhan_translate",
    "bodhan_verify_speech",
    "bodhan_verify_translation",
]
