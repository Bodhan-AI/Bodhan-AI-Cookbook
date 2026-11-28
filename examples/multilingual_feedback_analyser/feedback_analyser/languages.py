"""Script detection from Unicode ranges and the language tables the pipeline needs."""

from __future__ import annotations

import re

SCRIPT_RANGES = {
    "Devanagari": (0x0900, 0x097F),
    "Bengali": (0x0980, 0x09FF),
    "Gurmukhi": (0x0A00, 0x0A7F),
    "Gujarati": (0x0A80, 0x0AFF),
    "Odia": (0x0B00, 0x0B7F),
    "Tamil": (0x0B80, 0x0BFF),
    "Telugu": (0x0C00, 0x0C7F),
    "Kannada": (0x0C80, 0x0CFF),
    "Malayalam": (0x0D00, 0x0D7F),
    "Arabic": (0x0600, 0x06FF),
    "Ol Chiki": (0x1C50, 0x1C7F),
    "Meetei Mayek": (0xABC0, 0xABFF),
}

SCRIPT_DEFAULT_LANGUAGE = {
    "Devanagari": "hi",
    "Bengali": "bn",
    "Gurmukhi": "pa",
    "Gujarati": "gu",
    "Odia": "or",
    "Tamil": "ta",
    "Telugu": "te",
    "Kannada": "kn",
    "Malayalam": "ml",
    "Arabic": "ur",
    "Ol Chiki": "sat",
    "Meetei Mayek": "mni",
}

LANGUAGE_SCRIPT = {
    "as": "Bengali", "bn": "Bengali", "mni-Beng": "Bengali",
    "hi": "Devanagari", "mr": "Devanagari", "ne": "Devanagari", "sa": "Devanagari", "kok": "Devanagari",
    "mai": "Devanagari", "doi": "Devanagari", "brx": "Devanagari", "sd": "Devanagari", "ks-Deva": "Devanagari",
    "bho": "Devanagari", "bhb": "Devanagari",
    "pa": "Gurmukhi", "gu": "Gujarati", "or": "Odia", "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam",
    "ur": "Arabic", "ks": "Arabic", "sd-Arab": "Arabic", "sat": "Ol Chiki", "mni": "Meetei Mayek", "en": "Latin",
}

SHARED_SCRIPT_MARKERS = {
    "Devanagari": [("mr", re.compile(r"ळ|(?<![ऀ-ॿ])(?:आहे|नाही|आणि|होते|झाले|झाला|मला|खूप)(?![ऀ-ॿ])"))],
    "Bengali": [("as", re.compile(r"[ৰৱ]"))],
}

LANGUAGE_NAMES = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo", "doi": "Dogri", "en": "English",
    "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada", "kok": "Konkani", "ks": "Kashmiri", "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri", "mni-Beng": "Manipuri (Bengali script)", "mr": "Marathi",
    "ne": "Nepali", "or": "Odia", "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "sd-Arab": "Sindhi (Perso-Arabic)", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

TRANSLATE_LANGUAGES = frozenset(LANGUAGE_NAMES) - {"bhb", "bho"}
TRANSLITERATE_LANGUAGES = TRANSLATE_LANGUAGES - {"en"}

VOICES = {
    "as": "Prastuti", "bn": "Ishita", "brx": "Gwrbw", "doi": "Preeti", "en": "Kavya", "gu": "Dhara", "hi": "Kavya",
    "kn": "Deepika", "kok": "Anjali", "ks": "Zoon", "mai": "Vaidehi", "ml": "Lakshmi", "mni": "Thoibi", "mr": "Anagha",
    "ne": "Srijana", "or": "Itishree", "pa": "Kaur", "sa": "Bharati", "sat": "Phulmani", "sd": "Moomal", "ta": "Anitha",
    "te": "Sravani", "ur": "Saba",
}

INDIC_SHARE_TO_WIN = 0.3


def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for char in text:
        name = None
        if char.isascii():
            name = "Latin" if char.isalpha() else None
        else:
            code = ord(char)
            for script, (low, high) in SCRIPT_RANGES.items():
                if low <= code <= high:
                    name = script
                    break
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def dominant_script(text: str) -> str | None:
    counts = script_counts(text)
    total = sum(counts.values())
    if not total:
        return None
    indic = {name: count for name, count in counts.items() if name != "Latin"}
    if indic:
        best = max(indic, key=indic.get)
        if indic[best] / total >= INDIC_SHARE_TO_WIN:
            return best
    return max(counts, key=counts.get)


def language_for_script(script: str, text: str) -> str | None:
    for language, marker in SHARED_SCRIPT_MARKERS.get(script, []):
        if marker.search(text):
            return language
    return SCRIPT_DEFAULT_LANGUAGE.get(script)
