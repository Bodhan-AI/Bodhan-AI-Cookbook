"""Data models and constants for the Bodhan API."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .exceptions import BodhanValidationError

# Supported language codes from scripts/bodhan_api_rules.json (plain ISO codes)
# Supports 22 Indian languages plus English across 26 target-language codes
# (Kashmiri, Manipuri, and Sindhi each have two script variants).
# Note: 'bhb' (Bhili) and 'bho' (Bhojpuri) are speech-to-text only, not supported for translation.
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


def validate_translation_script(target_language: str, script: str | None) -> str | None:
    """Validate script compatibility with target language for Bodhan translation.

    Rules:
    - Supported scripts: 'native', 'roman', 'codemix'.
    - Script selection does not apply to target_language='en'.
    - 'roman' is rejected for English.
    - 'codemix' is unavailable for English, ks-Deva, and sd-Arab.
    - If script='native' or None is passed for English, returns None so script is omitted from HTTP dispatch.
    - For applicable Indic targets, returns the valid script string.

    Returns:
        The normalized script to include in outgoing requests, or None if script should be omitted.

    Raises:
        BodhanValidationError: If the script or language-script combination is invalid.
    """
    if script is None:
        return None

    if script not in SUPPORTED_SCRIPTS:
        raise BodhanValidationError(
            f"Invalid script '{script}'. Must be one of: {', '.join(sorted(SUPPORTED_SCRIPTS))}",
            parameter="script",
        )

    if target_language == "en":
        if script == "roman":
            raise BodhanValidationError(
                "Script 'roman' is not valid for English (target_language='en'). Script selection does not apply to English.",
                parameter="script",
            )
        if script == "codemix":
            raise BodhanValidationError(
                "Script 'codemix' is not available for English (target_language='en').",
                parameter="script",
            )
        # For script == 'native' targeting English, script does not apply; omit from request payload
        return None

    if script == "codemix" and target_language in ("ks-Deva", "sd-Arab"):
        raise BodhanValidationError(
            f"Script 'codemix' is not available for target language '{target_language}'.",
            parameter="script",
        )

    return script


@dataclass(frozen=True)
class OCRBlock:
    """A layout block detected on an OCR page.

    Attributes:
        order: Reading order sequence number.
        label: Semantic label (e.g., 'title', 'paragraph', 'table', 'figure').
        type: Documented API block type (e.g., 'text', 'table', 'formula', or None).
        bbox_xyxy: Pixel bounding box as [x1, y1, x2, y2] preserving numeric float precision.
        conf: Model confidence score between 0.0 and 1.0.
        text: Extracted text within the bounding block.
        raw: Full dictionary returned by the API for this block.
    """

    order: int
    label: str
    type: str | None = None
    bbox_xyxy: list[float | int] | None = None
    conf: float | None = None
    text: str = ""
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
        source_language: Optional source language code (note: Bodhan's documented translation API infers the source language automatically and does not guarantee detected language metadata in responses).
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
