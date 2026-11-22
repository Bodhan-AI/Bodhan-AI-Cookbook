"""Deterministic narration, used when the LLM is unavailable or its answer cannot be repaired."""

from __future__ import annotations

import re

from bodhan_lesson.models import Element, FigureDescription
from bodhan_lesson.text import speech

MAX_UNKNOWN_COMMANDS = 3  # an equation with more untranslatable commands is skipped, not garbled
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}")


def _table_speech(markdown: str) -> str:
    rows = [r for r in markdown.strip().split("\n") if r.strip() and not _SEPARATOR.match(r)]
    if not rows:
        return ""
    cells = [[speech.plain_speech(c) for c in row.strip().strip("|").split("|")] for row in rows]
    spoken = [f"Row {n}: " + ", ".join(c for c in row if c) + "." for n, row in enumerate(cells[1:], start=1)]
    return " ".join(["This table has the columns " + ", ".join(cells[0]) + ".", *spoken])


def fallback_text(element: Element, figure: FigureDescription | None, caption: str) -> tuple[str | None, str]:
    """Spoken text for one element, or (None, reason) when it cannot be spoken reliably."""
    if element.kind == "figure":
        if figure and figure.decorative:
            return None, "decorative figure"
        if figure and figure.description:
            return f"Look at the figure. {figure.description}", ""
        return (f"Look at {caption}.", "") if caption else (None, "figure without description or caption")
    if element.kind == "equation":
        spoken, unknown = speech.latex_to_speech(re.sub(r"\$+", " ", element.markdown))
        if unknown >= MAX_UNKNOWN_COMMANDS or not spoken:
            return None, "unspeakable equation"
        return spoken + ".", ""
    if element.kind == "table":
        spoken = _table_speech(element.markdown)
        return (spoken, "") if spoken else (None, "empty table")
    spoken = speech.plain_speech(element.markdown)
    if element.kind == "heading" and spoken:
        spoken += "."
    return (spoken, "") if spoken else (None, "empty element")
