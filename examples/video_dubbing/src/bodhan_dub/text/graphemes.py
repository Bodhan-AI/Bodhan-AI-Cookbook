"""
Grapheme cluster utilities for Indic and English text.
Uses regex `\\X` to ensure conjuncts, combining characters, and viramas are never split.
"""

from __future__ import annotations

from typing import List

import regex


def get_grapheme_clusters(text: str) -> List[str]:
    """Splits text into indivisible extended grapheme clusters using regex `\\X`."""
    return regex.findall(r"\X", text)


def wrap_text_graphemes(text: str, max_line_chars: int = 42, max_lines: int = 2) -> str:
    """
    Wraps text into at most `max_lines` without breaking words or grapheme clusters.
    """
    words = text.strip().split()
    if not words:
        return ""

    lines: List[str] = []
    current_line: List[str] = []
    current_len = 0

    for word in words:
        word_len = len(get_grapheme_clusters(word))
        # Check if word fits on current line
        space = 1 if current_line else 0
        if current_len + space + word_len <= max_line_chars:
            current_line.append(word)
            current_len += space + word_len
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_len = word_len

    if current_line:
        lines.append(" ".join(current_line))

    # If lines exceed max_lines, merge excess lines into the last line
    if len(lines) > max_lines:
        first = lines[0]
        rest = " ".join(lines[1:])
        return f"{first}\n{rest}"

    return "\n".join(lines)
