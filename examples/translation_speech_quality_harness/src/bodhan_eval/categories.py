"""Canonical error categories, severities and alias mapping for incoming labels.

Hidden-set labels may spell categories differently ("number or unit", "Term/Entity",
"truncation"), so every label is mapped through `canonical_category`.
"""

from __future__ import annotations

import re

TRANSLATION_CATEGORIES = (
    "omission",
    "addition",
    "mistranslation",
    "number",
    "term",
    "format",
    "wrong_language",
)
SPEECH_CATEGORIES = ("speech_omission", "speech_repetition", "speech_garbled")
CATEGORIES = TRANSLATION_CATEGORIES + SPEECH_CATEGORIES

SEVERITIES = ("minor", "major", "critical")
# MQM-style weights used for the aggregate quality score.
SEVERITY_WEIGHT = {"minor": 1.0, "major": 5.0, "critical": 10.0}

_ALIASES = {
    # translation
    "omission": "omission",
    "omitted": "omission",
    "missing": "omission",
    "addition": "addition",
    "added": "addition",
    "insertion": "addition",
    "hallucination": "addition",
    "mistranslation": "mistranslation",
    "wrong_meaning": "mistranslation",
    "meaning": "mistranslation",
    "accuracy": "mistranslation",
    "number": "number",
    "numbers": "number",
    "number_or_unit": "number",
    "unit": "number",
    "numeric": "number",
    "date": "number",
    "quantity": "number",
    "term": "term",
    "terminology": "term",
    "entity": "term",
    "term_or_entity": "term",
    "named_entity": "term",
    "name": "term",
    "format": "format",
    "formatting": "format",
    "markdown": "format",
    "latex": "format",
    "table": "format",
    "markup": "format",
    "wrong_language": "wrong_language",
    "wrong_script": "wrong_language",
    "wrong_language_or_script": "wrong_language",
    "untranslated": "wrong_language",
    "language": "wrong_language",
    "script": "wrong_language",
    # speech
    "speech_omission": "speech_omission",
    "truncation": "speech_omission",
    "omission_or_truncation": "speech_omission",
    "truncated": "speech_omission",
    "speech_truncation": "speech_omission",
    "repetition": "speech_repetition",
    "speech_repetition": "speech_repetition",
    "repeat": "speech_repetition",
    "loop": "speech_repetition",
    "garbled": "speech_garbled",
    "garbled_audio": "speech_garbled",
    "speech_garbled": "speech_garbled",
    "noise": "speech_garbled",
    "silence": "speech_garbled",
    "unintelligible": "speech_garbled",
}

_SPEECH_HINTS = ("speech", "audio", "tts", "speak", "wav")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.strip().lower()).strip("_")


def canonical_category(raw: str, *, area: str | None = None) -> str | None:
    """Map a free-form label type to a canonical category, or None if unrecognised.

    `area` (e.g. a label's "area"/"target"/"modality" field) disambiguates "omission",
    which exists for both translation and speech.
    """
    k = _key(raw)
    is_speech = bool(area) and any(h in _key(area) for h in _SPEECH_HINTS)
    if k.startswith(("speech_", "audio_")):
        is_speech = True
        k2 = k.split("_", 1)[1]
        k = k if k in _ALIASES else k2
    cat = _ALIASES.get(k)
    if cat is None:
        return None
    if is_speech and cat == "omission":
        return "speech_omission"
    return cat


def canonical_severity(raw: str | None) -> str:
    k = _key(raw or "major")
    if k in SEVERITIES:
        return k
    return {
        "low": "minor",
        "medium": "major",
        "high": "critical",
        "neutral": "minor",
        "severe": "critical",
    }.get(k, "major")


def area_of(category: str) -> str:
    return "speech" if category in SPEECH_CATEGORIES else "translation"
