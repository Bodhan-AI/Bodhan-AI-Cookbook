"""Error categories, severities and the aliases that map other label spellings onto them.

Aliases are data: a new label spelling in someone else's dataset is one dict entry here.
"""
from __future__ import annotations

TRANSLATION_CATEGORIES = ("omission", "addition", "mistranslation", "number", "term", "format", "wrong_language")
SPEECH_CATEGORIES = ("speech_omission", "speech_repetition", "speech_garbled")
CATEGORIES = TRANSLATION_CATEGORIES + SPEECH_CATEGORIES

SEVERITIES = ("minor", "major", "critical")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
MQM_WEIGHT = {"minor": 1.0, "major": 5.0, "critical": 10.0}

_ALIASES = {
    "omission": "omission",
    "deletion": "omission",
    "missing": "omission",
    "addition": "addition",
    "insertion": "addition",
    "hallucination": "addition",
    "mistranslation": "mistranslation",
    "meaning": "mistranslation",
    "accuracy": "mistranslation",
    "number": "number",
    "numbers": "number",
    "number_or_unit": "number",
    "unit": "number",
    "date": "number",
    "term": "term",
    "entity": "term",
    "term_or_entity": "term",
    "terminology": "term",
    "name": "term",
    "format": "format",
    "formatting": "format",
    "markdown": "format",
    "latex": "format",
    "wrong_language": "wrong_language",
    "wrong_language_or_script": "wrong_language",
    "wrong_script": "wrong_language",
    "script": "wrong_language",
    "untranslated": "wrong_language",
    "language": "wrong_language",
    "speech_omission": "speech_omission",
    "omission_or_truncation": "speech_omission",
    "truncation": "speech_omission",
    "speech_truncation": "speech_omission",
    "repetition": "speech_repetition",
    "speech_repetition": "speech_repetition",
    "loop": "speech_repetition",
    "garbled": "speech_garbled",
    "garbled_audio": "speech_garbled",
    "speech_garbled": "speech_garbled",
    "noise": "speech_garbled",
    "silence": "speech_garbled",
    "unintelligible": "speech_garbled",
}


def canonical_category(label_type: str, area: str | None = None) -> str:
    key = label_type.strip().lower().replace("-", "_").replace(" ", "_")
    speech = (area or "").lower() in ("speech", "audio") or key.startswith(("speech", "audio"))
    if speech:
        stripped = key.removeprefix("speech_").removeprefix("audio_")
        if stripped in ("omission", "truncation", "omission_or_truncation", "deletion", "missing"):
            return "speech_omission"
        if stripped in ("repetition", "loop", "repeat"):
            return "speech_repetition"
        return "speech_garbled" if stripped not in _ALIASES else _ALIASES.get(key, _ALIASES[stripped])
    return _ALIASES.get(key, key)


def area_of(category: str) -> str:
    return "speech" if category in SPEECH_CATEGORIES else "translation"


def max_severity(*sevs: str) -> str:
    return max(sevs, key=lambda s: SEVERITY_RANK.get(s, 1))
