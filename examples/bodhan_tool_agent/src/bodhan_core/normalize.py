"""
Text normalization and verification metrics for Indic language processing.
"""
from __future__ import annotations

import re
import unicodedata

# Mapping from Indic numerals to standard ASCII 0-9
_INDIC_DIGIT_MAP: dict[int, str] = {
    # Devanagari (0966 - 096F)
    0x0966: "0", 0x0967: "1", 0x0968: "2", 0x0969: "3", 0x096A: "4",
    0x096B: "5", 0x096C: "6", 0x096D: "7", 0x096E: "8", 0x096F: "9",
    # Bengali (09E6 - 09EF)
    0x09E6: "0", 0x09E7: "1", 0x09E8: "2", 0x09E9: "3", 0x09EA: "4",
    0x09EB: "5", 0x09EC: "6", 0x09ED: "7", 0x09EE: "8", 0x09EF: "9",
    # Gurmukhi (0A66 - 0A6F)
    0x0A66: "0", 0x0A67: "1", 0x0A68: "2", 0x0A69: "3", 0x0A6A: "4",
    0x0A6B: "5", 0x0A6C: "6", 0x0A6D: "7", 0x0A6E: "8", 0x0A6F: "9",
    # Gujarati (0AE6 - 0AEF)
    0x0AE6: "0", 0x0AE7: "1", 0x0AE8: "2", 0x0AE9: "3", 0x0AEA: "4",
    0x0AEB: "5", 0x0AEC: "6", 0x0AED: "7", 0x0AEE: "8", 0x0AEF: "9",
    # Odia (0B66 - 0B6F)
    0x0B66: "0", 0x0B67: "1", 0x0B68: "2", 0x0B69: "3", 0x0B6A: "4",
    0x0B6B: "5", 0x0B6C: "6", 0x0B6D: "7", 0x0B6E: "8", 0x0B6F: "9",
    # Tamil (0BE6 - 0BEF)
    0x0BE6: "0", 0x0BE7: "1", 0x0BE8: "2", 0x0BE9: "3", 0x0BEA: "4",
    0x0BEB: "5", 0x0BEC: "6", 0x0BED: "7", 0x0BEE: "8", 0x0BEF: "9",
    # Telugu (0C66 - 0C6F)
    0x0C66: "0", 0x0C67: "1", 0x0C68: "2", 0x0C69: "3", 0x0C6A: "4",
    0x0C6B: "5", 0x0C6C: "6", 0x0C6D: "7", 0x0C6E: "8", 0x0C6F: "9",
    # Kannada (0CE6 - 0CEF)
    0x0CE6: "0", 0x0CE7: "1", 0x0CE8: "2", 0x0CE9: "3", 0x0CEA: "4",
    0x0CEB: "5", 0x0CEC: "6", 0x0CED: "7", 0x0CEE: "8", 0x0CEF: "9",
    # Malayalam (0D66 - 0D6F)
    0x0D66: "0", 0x0D67: "1", 0x0D68: "2", 0x0D69: "3", 0x0D6A: "4",
    0x0D6B: "5", 0x0D6C: "6", 0x0D6D: "7", 0x0D6E: "8", 0x0D6F: "9",
}


def normalize_text(text: str) -> str:
    """Normalize text with Unicode NFC, collapsed whitespace, and trimmed borders."""
    if not text:
        return ""
    nfc = unicodedata.normalize("NFC", text)
    # Collapse multiple whitespaces/newlines to single space
    collapsed = re.sub(r"\s+", " ", nfc)
    return collapsed.strip()


def digit_normalize(text: str) -> str:
    """Replace all Indic script numerals with standard ASCII 0-9."""
    if not text:
        return ""
    return text.translate(_INDIC_DIGIT_MAP)


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute character-level Levenshtein edit distance."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def compute_cer(reference: str, hypothesis: str) -> float:
    """
    Compute Character Error Rate (CER): edit_distance / len(reference).

    Both inputs are normalized with normalize_text and digit_normalize first.
    Returns 0.0 for identical strings or both empty.
    """
    norm_ref = digit_normalize(normalize_text(reference))
    norm_hyp = digit_normalize(normalize_text(hypothesis))

    if not norm_ref and not norm_hyp:
        return 0.0
    if not norm_ref:
        return 1.0

    dist = _levenshtein_distance(norm_ref, norm_hyp)
    return dist / len(norm_ref)
