"""Reference-free evaluation by back-translation.

The Indic translation is translated back to English with Indic-Translate
and compared with the *original English source*. The translation itself is
never used as a reference.

A round trip has two error sources (forward and backward translation), and
a correct translation can come back paraphrased, so apart from numbers every
signal here is a ``review`` signal rather than a hard failure.
"""

from __future__ import annotations

import re

from bodhan_eval.checks import (
    check_numbers,
    extract_latin_entities,
    extract_verbatim_entities,
    make_check,
)
from bodhan_eval.metrics import chrf

# chrF(back-translation, source) below this is flagged for review.
ROUND_TRIP_CHRF_THRESHOLD = 0.5
# Fraction of source content words that must come back.
CONTENT_COVERAGE_THRESHOLD = 0.6

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "will", "shall",
    "to", "of", "in", "on", "at", "for", "by", "with", "from", "and", "or",
    "this", "that", "these", "those", "it", "its", "as", "has", "have", "had",
    "our", "your", "my", "their", "his", "her", "we", "you", "they", "i",
    "please", "can", "should", "must", "do", "does", "not", "than", "more",
}


def content_words(text: str) -> list[str]:
    return [
        word for word in re.findall(r"[a-z]+", text.lower())
        if word not in _STOPWORDS and len(word) > 2
    ]


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def evaluate_back_translation(source_text: str, back_translation: str) -> list[dict]:
    """Compare an English back-translation with the English source."""
    checks = []

    number_check = check_numbers(source_text, back_translation, "en")
    checks.append({**number_check, "category": "round_trip_number_mismatch"})

    score = chrf(back_translation, source_text)
    source_words = content_words(source_text)
    back_stems = {_stem(w) for w in content_words(back_translation)}
    missing_words = [w for w in source_words if _stem(w) not in back_stems]
    coverage = (
        1 - len(missing_words) / len(source_words) if source_words else 1.0
    )

    similar = score >= ROUND_TRIP_CHRF_THRESHOLD and coverage >= CONTENT_COVERAGE_THRESHOLD
    checks.append(make_check(
        "round_trip_similarity",
        "pass" if similar else "review",
        "medium",
        {
            "chrf": score,
            "chrf_threshold": ROUND_TRIP_CHRF_THRESHOLD,
            "content_word_coverage": round(coverage, 3),
            "coverage_threshold": CONTENT_COVERAGE_THRESHOLD,
            "missing_content_words": missing_words,
        },
    ))

    entities = extract_verbatim_entities(source_text) + extract_latin_entities(source_text)
    if entities:
        lowered = back_translation.lower()
        missing_entities = [e for e in entities if e.lower() not in lowered]
        checks.append(make_check(
            "round_trip_entity",
            "review" if missing_entities else "pass",
            "low",
            {"entities": entities, "missing_after_round_trip": missing_entities},
        ))

    return checks


def evaluate_round_trip(
    client,
    source_text: str,
    translated_text: str,
) -> dict:
    """Translate an Indic translation back to English and inspect it.

    ``client`` is anything with ``translate(text, target_language)``: the raw
    :class:`BodhanClient` or the caching :class:`CachedTranslator`.
    """
    response = client.translate(translated_text, "en")
    back_translation = response["translation"]

    checks = evaluate_back_translation(source_text, back_translation)

    if response.get("truncated"):
        checks.append(make_check(
            "round_trip_truncated",
            "review",
            "medium",
            {"note": "Back-translation hit the output length limit."},
        ))

    return {
        "back_translation": back_translation,
        "truncated": response.get("truncated", False),
        "cached": response.get("cached", False),
        "checks": checks,
        "usage": response.get("usage", {}),
    }
