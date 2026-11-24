"""
Character Error Rate (CER) and omission detection utilities.
Uses rapidfuzz Levenshtein distance on normalized Indic/English text.
"""

from __future__ import annotations

from typing import Tuple

import rapidfuzz.distance.Levenshtein as lev

from bodhan_dub.text.normalize import normalize_text_for_comparison


def compute_cer(reference: str, hypothesis: str) -> float:
    """
    Computes Character Error Rate:
    CER = Levenshtein_distance(ref_norm, hyp_norm) / max(1, len(ref_norm))
    Normalizes both sides to Unicode NFC, Indic digits -> ASCII, collapses whitespace,
    and strips punctuation without altering combining vowel marks.
    """
    ref_norm = normalize_text_for_comparison(reference, strip_punct=True, normalize_digits=True)
    hyp_norm = normalize_text_for_comparison(hypothesis, strip_punct=True, normalize_digits=True)

    if not ref_norm:
        return 0.0 if not hyp_norm else 1.0

    edit_dist = lev.distance(ref_norm, hyp_norm)
    return float(edit_dist) / float(len(ref_norm))


def detect_tail_omission(
    reference: str,
    hypothesis: str,
    tail_fraction: float = 0.15,
    match_threshold: float = 0.50,
) -> Tuple[bool, str]:
    """
    Checks if the final ~15% of reference text was truncated or omitted by the TTS model.
    Returns (is_omitted, tail_text).
    """
    ref_norm = normalize_text_for_comparison(reference, strip_punct=True, normalize_digits=True)
    hyp_norm = normalize_text_for_comparison(hypothesis, strip_punct=True, normalize_digits=True)

    if len(ref_norm) < 20:
        return False, ""

    tail_len = max(5, int(len(ref_norm) * tail_fraction))
    tail = ref_norm[-tail_len:].strip()

    if not tail:
        return False, ""

    # Check if tail appears anywhere near the end of hypothesis
    hyp_tail = hyp_norm[-int(tail_len * 1.5) :] if len(hyp_norm) >= tail_len else hyp_norm
    similarity = lev.normalized_similarity(tail, hyp_tail)

    if similarity < match_threshold and tail not in hyp_norm:
        return True, tail

    return False, ""
