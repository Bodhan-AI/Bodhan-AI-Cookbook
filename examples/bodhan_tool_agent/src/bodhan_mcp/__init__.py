"""Bodhan MCP package."""

from .server import MCPServer, TOOLS_REGISTRY
from .client import BodhanClient
from .tools import (
    bodhan_transcribe,
    bodhan_translate,
    bodhan_speak,
    bodhan_ocr,
    bodhan_inspect_media,
    bodhan_detect_language,
    bodhan_list_voices,
)

__all__ = [
    "MCPServer",
    "TOOLS_REGISTRY",
    "BodhanClient",
    "bodhan_transcribe",
    "bodhan_translate",
    "bodhan_speak",
    "bodhan_ocr",
    "bodhan_inspect_media",
    "bodhan_detect_language",
    "bodhan_list_voices",
]
