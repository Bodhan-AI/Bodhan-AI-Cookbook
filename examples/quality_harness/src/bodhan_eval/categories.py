"""Error categories: our names, the brief's names, and the many ways a label file may spell them.

Flags carry our short `category` plus `area` and `error_type` in the brief's own words, so a reader
(or a scorer) needn't learn our names. Labels from any source are mapped onto ours here; a label
that can't be told apart ("omission" with no hint of text or speech) accepts either.
"""

from __future__ import annotations

import re

ERROR_TYPES = {  # category -> (area, the brief's error type)
    "omission": ("translation", "Omission"),
    "addition": ("translation", "Addition"),
    "mistranslation": ("translation", "Mistranslation"),
    "number": ("translation", "Number or unit"),
    "term": ("translation", "Term or entity"),
    "format": ("translation", "Format"),
    "wrong_language": ("translation", "Wrong language or script"),
    "speech_omission": ("speech", "Omission or truncation"),
    "speech_repetition": ("speech", "Repetition"),
    "speech_garbled": ("speech", "Garbled audio"),
}

_ALIASES = {
    "omission": ["omission", "omitted", "missing", "dropped"],
    "addition": ["addition", "added", "insertion", "extra", "hallucination"],
    "mistranslation": ["mistranslation", "accuracy", "meaning", "wrong_meaning", "wrong_sense", "negation"],
    "number": ["number", "numbers", "number_unit", "number_or_unit", "unit", "units", "date", "quantity", "numeric"],
    "term": ["term", "terms", "term_entity", "term_or_entity", "entity", "entities", "name", "terminology", "named_entity"],
    "format": ["format", "formatting", "markdown", "latex", "table", "markup", "broken_format"],
    "wrong_language": ["wrong_language", "wrong_language_or_script", "wrong_script", "script", "language",
                       "untranslated", "wrong_lang", "code_switching"],
    "speech_omission": ["speech_omission", "truncation", "truncated", "speech_truncation", "omission_or_truncation",
                        "audio_omission", "not_spoken", "audio_truncation"],
    "speech_repetition": ["speech_repetition", "repetition", "repeated", "loop", "looping", "audio_repetition"],
    "speech_garbled": ["speech_garbled", "garbled", "garbled_audio", "noise", "silence", "long_silence",
                       "unintelligible", "clipping", "distortion", "audio_quality", "artifact", "artefact"],
}
ALIASES = {a: cat for cat, names in _ALIASES.items() for a in names}
SPEECH_HINTS = {"speech", "audio", "tts", "asr"}


def key(name: object) -> str:
    """'Number or unit' -> 'number_or_unit'."""
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")


def is_speech_label(lab: dict) -> bool:
    hints = {key(lab.get(f)) for f in ("area", "modality", "kind", "source", "domain")}
    return bool(hints & SPEECH_HINTS) or any(f in lab for f in ("time", "audio_span", "start_s", "time_span"))


def label_categories(lab: dict) -> tuple[str, set[str]]:
    """(the category to report it under, the flag categories that count as right) for one label."""
    raw = key(lab.get("type") or lab.get("category") or lab.get("error_type"))
    speech = is_speech_label(lab)
    if raw.startswith(("speech_", "audio_")) or raw in ("speech", "audio"):
        speech = True
    cat = ALIASES.get(raw) or ALIASES.get(raw.removeprefix("speech_").removeprefix("audio_")) or raw or "other"
    if speech and cat == "omission":
        cat = "speech_omission"
    if speech and cat in ("number", "term", "mistranslation", "addition"):
        cat = "speech_garbled"  # wrong words in the audio: our closest speech category
    if cat == "omission":  # no hint of speech: it may still be words missing from the audio, so either is right
        return "omission", {"omission", "speech_omission"}
    return cat, {cat}


def describe(category: str) -> dict:
    area, error_type = ERROR_TYPES.get(category, ("translation", "Other"))
    return {"area": area, "error_type": error_type}
