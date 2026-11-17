from __future__ import annotations

import unicodedata
from collections import Counter

from bodhan_mcp.core.languages import SCRIPT_LANGUAGES, SCRIPT_RANGES


def script_of(char: str) -> str | None:
    point = ord(char)
    for low, high, script in SCRIPT_RANGES:
        if low <= point <= high:
            return script
    return None


def script_counts(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for char in text:
        if unicodedata.category(char)[0] in "LM":
            script = script_of(char)
            if script is not None:
                counts[script] += 1
    return counts


def dominant_script(text: str) -> str | None:
    counts = script_counts(text)
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def language_from_script(script: str | None) -> tuple[str | None, list[str]]:
    if script is None:
        return None, []
    candidates = list(SCRIPT_LANGUAGES.get(script, ()))
    if len(candidates) == 1 and script != "Latin":
        return candidates[0], []
    return None, candidates
