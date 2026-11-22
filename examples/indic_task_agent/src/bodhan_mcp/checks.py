"""Plain-code quality checks for generated speech and translations.

The thresholds are starting guesses: calibrate them against a few real runs.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

from .scripts import detect_script, script_matches_language

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\ufeff"}
_PLACEHOLDER_OUTPUT = re.compile(
    r"\b(?:will be provided after|placeholder|insert\s+\w+\s+here|"
    r"please provide the (?:source )?text|text to be translated)\b",
    re.IGNORECASE,
)
_PLACEHOLDER_INPUT = re.compile(
    r"^\s*(?:(?:source|transcript|input)\s+)?text\s+will\s+be\s+provided(?:\s+after\s+[\w -]+)?[.! ]*$"
    r"|^\s*(?:placeholder|(?:insert|enter|add)\s+(?:the\s+)?(?:source\s+)?text(?:\s+here)?)[.! ]*$"
    r"|^\s*(?:\[[^\]]+\]|\{\{[^}]+\}\}|<[^>]+>)\s*$",
    re.IGNORECASE,
)


def is_placeholder_text(text: str) -> bool:
    return bool(_PLACEHOLDER_INPUT.fullmatch(text or ""))


def normalize_for_compare(text: str) -> str:
    """Lower-case, drop punctuation and zero-width marks, collapse spaces."""
    text = unicodedata.normalize("NFC", text or "").casefold()
    chars = []
    for ch in text:
        if ch in _ZERO_WIDTH:
            continue
        chars.append(ch if unicodedata.category(ch)[0] in ("L", "M", "N") else " ")
    return " ".join("".join(chars).split())


def compare_speech(expected: str, heard: str, min_ratio: float = 0.75,
                   length_range: tuple[float, float] = (0.7, 1.4)) -> dict:
    """Compare the text we asked Indic-Speak to read with what a transcription heard.

    Flags omissions (heard much shorter), repetition (heard much longer) and low similarity.
    """
    a, b = normalize_for_compare(expected), normalize_for_compare(heard)
    if not a:
        return {"ok": False, "ratio": 0.0, "length_ratio": 0.0, "issues": ["Expected text is empty."]}
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    length_ratio = len(b) / len(a)
    issues = []
    if length_ratio < length_range[0]:
        issues.append(f"Heard text is much shorter ({length_ratio:.0%} of expected): content may be omitted.")
    if length_ratio > length_range[1]:
        issues.append(f"Heard text is much longer ({length_ratio:.0%} of expected): content may be repeated.")
    if ratio < min_ratio:
        issues.append(f"Low similarity ({ratio:.0%}, need {min_ratio:.0%}).")
    return {"ok": not issues, "ratio": round(ratio, 3), "length_ratio": round(length_ratio, 3), "issues": issues}


def check_translation(source: str, translated: str, target_language: str,
                      truncated: bool = False, length_range: tuple[float, float] = (0.25, 4.0),
                      target_script: str = "native") -> dict:
    """Sanity-check a translation: not empty, not truncated, right script, plausible length."""
    issues = []
    if truncated:
        issues.append("The API reported the output was truncated: translation is incomplete.")
    if not (translated or "").strip():
        issues.append("Translation is empty.")
    else:
        if _PLACEHOLDER_OUTPUT.search(translated) or is_placeholder_text(translated):
            issues.append("Output contains placeholder text rather than a translation.")
        if not script_matches_language(translated, target_language, output_script=target_script):
            found = detect_script(translated)["script"]
            issues.append(
                f"Output is mostly {found or 'unknown'} script, not the "
                f"{target_script} script expected for '{target_language}'."
            )
        src_len = len(normalize_for_compare(source))
        if src_len:
            ratio = len(normalize_for_compare(translated)) / src_len
            if not length_range[0] <= ratio <= length_range[1]:
                issues.append(f"Length ratio {ratio:.2f} is implausible for a translation.")
    return {"ok": not issues, "issues": issues}
