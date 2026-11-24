"""
Text normalization and repetition detection utilities for ASR and speech QA.
Handles Unicode NFC, Indic numeral to ASCII conversion, punctuation stripping,
and n-gram loop detection.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

# Mapping of Indic numerals to standard ASCII 0-9
INDIC_DIGIT_MAP = {
    # Devanagari (Hindi, Marathi, Sanskrit, etc.)
    0x0966: "0",
    0x0967: "1",
    0x0968: "2",
    0x0969: "3",
    0x096A: "4",
    0x096B: "5",
    0x096C: "6",
    0x096D: "7",
    0x096E: "8",
    0x096F: "9",
    # Bengali & Assamese
    0x09E6: "0",
    0x09E7: "1",
    0x09E8: "2",
    0x09E9: "3",
    0x09EA: "4",
    0x09EB: "5",
    0x09EC: "6",
    0x09ED: "7",
    0x09EE: "8",
    0x09EF: "9",
    # Gurmukhi (Punjabi)
    0x0A66: "0",
    0x0A67: "1",
    0x0A68: "2",
    0x0A69: "3",
    0x0A6A: "4",
    0x0A6B: "5",
    0x0A6C: "6",
    0x0A6D: "7",
    0x0A6E: "8",
    0x0A6F: "9",
    # Gujarati
    0x0AE6: "0",
    0x0AE7: "1",
    0x0AE8: "2",
    0x0AE9: "3",
    0x0AEA: "4",
    0x0AEB: "5",
    0x0AEC: "6",
    0x0AED: "7",
    0x0AEE: "8",
    0x0AEF: "9",
    # Odia
    0x0B66: "0",
    0x0B67: "1",
    0x0B68: "2",
    0x0B69: "3",
    0x0B6A: "4",
    0x0B6B: "5",
    0x0B6C: "6",
    0x0B6D: "7",
    0x0B6E: "8",
    0x0B6F: "9",
    # Tamil (1-9)
    0x0BE7: "1",
    0x0BE8: "2",
    0x0BE9: "3",
    0x0BEA: "4",
    0x0BEB: "5",
    0x0BEC: "6",
    0x0BED: "7",
    0x0BEE: "8",
    0x0BEF: "9",
    # Telugu
    0x0C66: "0",
    0x0C67: "1",
    0x0C68: "2",
    0x0C69: "3",
    0x0C6A: "4",
    0x0C6B: "5",
    0x0C6C: "6",
    0x0C6D: "7",
    0x0C6E: "8",
    0x0C6F: "9",
    # Kannada
    0x0CE6: "0",
    0x0CE7: "1",
    0x0CE8: "2",
    0x0CE9: "3",
    0x0CEA: "4",
    0x0CEB: "5",
    0x0CEC: "6",
    0x0CED: "7",
    0x0CEE: "8",
    0x0CEF: "9",
    # Malayalam
    0x0D66: "0",
    0x0D67: "1",
    0x0D68: "2",
    0x0D69: "3",
    0x0D6A: "4",
    0x0D6B: "5",
    0x0D6C: "6",
    0x0D6D: "7",
    0x0D6E: "8",
    0x0D6F: "9",
    # Arabic-Indic (Urdu)
    0x0660: "0",
    0x0661: "1",
    0x0662: "2",
    0x0663: "3",
    0x0664: "4",
    0x0665: "5",
    0x0666: "6",
    0x0667: "7",
    0x0668: "8",
    0x0669: "9",
}


def normalize_indic_digits(text: str) -> str:
    """Replaces all Indic and Arabic-Indic numerals with standard ASCII 0-9."""
    chars = []
    for char in text:
        cp = ord(char)
        if cp in INDIC_DIGIT_MAP:
            chars.append(INDIC_DIGIT_MAP[cp])
        else:
            chars.append(char)
    return "".join(chars)


def normalize_text_for_comparison(
    text: str,
    strip_punct: bool = True,
    normalize_digits: bool = True,
) -> str:
    """
    Standard text normalization:
    1. Unicode NFC
    2. Convert Indic digits to ASCII (optional)
    3. Strip punctuation marks (does NOT strip combining vowel marks)
    4. Collapse whitespace
    """
    if not text:
        return ""

    # 1. Unicode NFC
    norm = unicodedata.normalize("NFC", text)

    # 2. Digits
    if normalize_digits:
        norm = normalize_indic_digits(norm)

    # 3. Strip punctuation
    if strip_punct:
        # Keep letters, numbers, spaces, and combining marks (category starting with M)
        # Strip categories P (punctuation) and S (symbols)
        cleaned_chars = []
        for ch in norm:
            cat = unicodedata.category(ch)
            if cat.startswith("P") or cat.startswith("S"):
                cleaned_chars.append(" ")
            else:
                cleaned_chars.append(ch)
        norm = "".join(cleaned_chars)

    # 4. Collapse whitespace and lowercase
    norm = re.sub(r"\s+", " ", norm).strip().lower()
    return norm


def detect_repeated_ngrams(
    text: str,
    min_n: int = 1,
    max_n: int = 4,
    repeat_threshold: int = 3,
) -> Tuple[bool, Optional[str]]:
    """
    Detects if an n-gram repeats unnaturally (e.g. ASR looping hallucination).
    Returns (is_looping, repeated_phrase).
    """
    words = text.strip().split()
    if len(words) < min_n * repeat_threshold:
        return False, None

    for n in range(min_n, max_n + 1):
        for i in range(len(words) - n + 1):
            ngram = tuple(words[i : i + n])
            count = 1
            idx = i + n
            while idx + n <= len(words) and tuple(words[idx : idx + n]) == ngram:
                count += 1
                idx += n
                if count >= repeat_threshold:
                    phrase = " ".join(ngram)
                    return True, phrase

    return False, None
