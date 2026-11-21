"""Data models and constants for the Bodhan API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Supported language codes from scripts/bodhan_api_rules.json (plain ISO codes)
SUPPORTED_TRANSLATION_LANGUAGES = frozenset({
    "as",
    "bn",
    "brx",
    "doi",
    "en",
    "gu",
    "hi",
    "kn",
    "kok",
    "ks",
    "ks-Deva",
    "mai",
    "ml",
    "mni",
    "mni-Beng",
    "mr",
    "ne",
    "or",
    "pa",
    "sa",
    "sat",
    "sd",
    "sd-Arab",
    "ta",
    "te",
    "ur",
})

# Supported scripts for indic-translate
SUPPORTED_SCRIPTS = frozenset({"native", "roman", "codemix"})

# Supported table formatting outputs for indic-ocr
SUPPORTED_TABLE_FORMATS = frozenset({"html", "markdown"})


@dataclass(frozen=True)
class OCRBlock:
    """A layout block detected on an OCR page.

    Attributes:
        order: Reading order sequence number.
        label: Semantic label (e.g., 'title', 'paragraph', 'table', 'figure').
        bbox_xyxy: Pixel bounding box as [x1, y1, x2, y2].
        conf: Model confidence score between 0.0 and 1.0.
        text: Extracted text within the bounding block.
        raw: Full dictionary returned by the API for this block.
    """

    order: int
    label: str
    bbox_xyxy: list[int]
    conf: float
    text: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OCRResponse:
    """Structured response from Bodhan indic-ocr.

    Attributes:
        text: Full extracted document text in Markdown.
        blocks: List of structured layout blocks detected on the page.
        model: Model identifier used (e.g. 'indic-ocr').
        truncated: Whether generation was truncated (finish_reason == 'length').
        finish_reason: Model completion termination reason ('stop', 'length', etc.).
        usage: Token/page usage dictionary if provided by the API.
        raw_response: Complete raw JSON response dictionary.
    """

    text: str
    blocks: list[OCRBlock]
    model: str = "indic-ocr"
    truncated: bool = False
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TranslationResponse:
    """Structured response from Bodhan indic-translate.

    Attributes:
        text: Translated text.
        target_language: Target language ISO code.
        source_language: Detected source language if reported by the API.
        script: Script style used (native, roman, or codemix).
        truncated: Whether the response hit maximum output tokens.
        usage: Token usage information.
        raw_response: Complete raw JSON response dictionary.
    """

    text: str
    target_language: str
    source_language: str | None = None
    script: str | None = None
    truncated: bool = False
    usage: dict[str, Any] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)
