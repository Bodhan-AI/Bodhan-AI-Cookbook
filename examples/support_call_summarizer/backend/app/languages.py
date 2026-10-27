"""Languages supported end to end, from the Bodhan API docs (checked 2026-10-20).

``indic-transcribe`` accepts every code below. ``/translate`` accepts all of them
except Bhili and Bhojpuri, so those calls get an original-language transcript only.
"""

from __future__ import annotations

from .models import Language

_LANGUAGES: dict[str, str] = {
    "as": "Assamese",
    "bn": "Bengali",
    "bhb": "Bhili",
    "bho": "Bhojpuri",
    "brx": "Bodo",
    "doi": "Dogri",
    "en": "English",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "ks": "Kashmiri",
    "kok": "Konkani",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}

TRANSCRIBE_ONLY = frozenset({"bhb", "bho"})


def is_supported(code: str) -> bool:
    return code in _LANGUAGES


def translation_supported(code: str | None) -> bool:
    return code not in TRANSCRIBE_ONLY


def language_name(code: str | None) -> str | None:
    return _LANGUAGES.get(code) if code else None


def all_languages() -> list[Language]:
    return [
        Language(code=code, name=name, translation=translation_supported(code))
        for code, name in sorted(_LANGUAGES.items(), key=lambda item: item[1])
    ]
