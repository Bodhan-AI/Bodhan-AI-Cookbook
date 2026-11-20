"""Local text comparison used to check generated speech (no API calls, no cost)."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from typing import Any


def normalize_for_compare(text: str) -> str:
    """Lowercase, normalise Unicode, drop punctuation and symbols, collapse spaces.

    Categories starting with P (punctuation) and S (symbols) are removed. Letters (L),
    combining marks (M, e.g. Hindi vowel signs) and digits (N) are kept. A simple
    regex like [^\\w\\s] would wrongly delete the vowel signs.
    """
    t = unicodedata.normalize("NFC", text).casefold()
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] not in "PS")
    return " ".join(t.split())


def compare_texts(expected: str, heard: str, threshold: float = 0.8) -> dict[str, Any]:
    """Compare what we asked the voice to say with what a transcriber heard.

    recall    = how much of the expected words were heard (low  -> content was omitted)
    precision = how much of the heard words were expected (low  -> content was repeated/added)
    """
    a, b = normalize_for_compare(expected), normalize_for_compare(heard)
    ew, hw = a.split(), b.split()
    sm = SequenceMatcher(None, ew, hw, autojunk=False)
    matched = sum(block.size for block in sm.get_matching_blocks())
    recall = matched / len(ew) if ew else 1.0
    precision = matched / len(hw) if hw else (1.0 if not ew else 0.0)
    char_ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    missing = [w for tag, i1, i2, _, _ in sm.get_opcodes() if tag in ("delete", "replace") for w in ew[i1:i2]]
    return {
        "passed": min(recall, precision) >= threshold,
        "word_recall": round(recall, 3),
        "word_precision": round(precision, 3),
        "char_similarity": round(char_ratio, 3),
        "threshold": threshold,
        "missing_words_sample": missing[:10],
    }