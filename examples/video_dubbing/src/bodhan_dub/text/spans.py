"""
Protected spans and terminology consistency utilities.
Protects named entities and glossary terms with placeholders during translation,
restores canonical renderings, and validates number preservation.
"""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from bodhan_dub.text.normalize import normalize_indic_digits


def protect_terms(
    text: str,
    terms: List[str],
) -> Tuple[str, Dict[str, str]]:
    """
    Replaces glossary terms in text with numbered placeholders (__TERM_0__, etc.).
    Terms are sorted by length descending to prioritize longer phrases over substrings.
    Returns (protected_text, {placeholder: original_term}).
    """
    if not terms or not text:
        return text, {}

    # Sort descending by length
    sorted_terms = sorted(set(terms), key=len, reverse=True)
    placeholder_map: Dict[str, str] = {}
    protected = text

    idx = 0
    for term in sorted_terms:
        term_clean = term.strip()
        if not term_clean:
            continue

        # Match whole word boundary (case-insensitive)
        pattern = re.compile(rf"\b{re.escape(term_clean)}\b", re.IGNORECASE)
        if pattern.search(protected):
            ph = f"__TERM_{idx}__"
            placeholder_map[ph] = term_clean
            protected = pattern.sub(ph, protected)
            idx += 1

    return protected, placeholder_map


def restore_terms(
    translated_text: str,
    placeholder_map: Dict[str, str],
    canonical_translations: Dict[str, str],
) -> Tuple[str, bool]:
    """
    Substitutes placeholders with their canonical translations.
    Verifies that every placeholder survived translation exactly once.
    Returns (restored_text, all_placeholders_survived).
    """
    if not placeholder_map:
        return translated_text, True

    restored = translated_text
    survived = True

    for ph, orig_term in placeholder_map.items():
        # Check count of placeholder in translated text
        # Accept minor spacing variations like "__ TERM_0 __" or "__TERM_0__"
        fuzzy_pattern = re.compile(rf"__\s*{re.escape(ph.strip('_'))}\s*__", re.IGNORECASE)
        matches = fuzzy_pattern.findall(restored)

        canonical = canonical_translations.get(orig_term.lower(), orig_term)

        if len(matches) == 1:
            restored = fuzzy_pattern.sub(canonical, restored, count=1)
        elif len(matches) == 0:
            survived = False
        else:
            # Duplicated placeholder: substitute all but flag as imperfect
            restored = fuzzy_pattern.sub(canonical, restored)
            survived = False

    return restored, survived


def extract_numbers(text: str) -> List[str]:
    """
    Extracts numerical tokens from text after Indic-digit normalization.
    Returns list of normalized number strings (e.g. ['2026', '3.14', '10']).
    """
    if not text:
        return []
    ascii_text = normalize_indic_digits(text)
    # Match numbers (integers or decimals)
    matches = re.findall(r"\b\d+(?:\.\d+)?\b", ascii_text)
    return matches


def verify_numbers_preserved(source_en: str, target_indic: str) -> bool:
    """
    Verifies that all significant numbers in English source text are preserved in target text.
    """
    src_nums = extract_numbers(source_en)
    if not src_nums:
        return True  # No numbers to verify

    tgt_nums = extract_numbers(target_indic)
    tgt_set: Set[str] = set(tgt_nums)

    # Check each source number exists in target numbers
    for num in src_nums:
        if num not in tgt_set:
            return False

    return True
