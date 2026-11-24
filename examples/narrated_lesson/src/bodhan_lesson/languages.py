"""Supported language codes s per Bodhan API docs / cookbook rules.

--langs must be narratable (Indic-Speak). --source-lang may be any translate
code (English or Indic, including script variants). Reviewers can pass any of
these; we do not hardcode only hi/ta/bn.
"""

from __future__ import annotations

# From scripts/bodhan_api_rules.json → indic-speak.languages
SPEAK_LANGS: frozenset[str] = frozenset(
    {
        "as",
        "bn",
        "brx",
        "doi",
        "en",
        "gu",
        "hi",
        "kn",
        "kok",
        "ks",
        "mai",
        "ml",
        "mni",
        "mr",
        "ne",
        "or",
        "pa",
        "sa",
        "sat",
        "sd",
        "ta",
        "te",
        "ur",
    }
)

# From scripts/bodhan_api_rules.json → indic-translate.languages
TRANSLATE_LANGS: frozenset[str] = frozenset(
    {
        "as",
        "bn",
        "brx",
        "doi",
        "en",
        "gu",
        "hi",
        "kn",
        "kok",
        "ks",
        "ks-Deva",
        "mai",
        "ml",
        "mni",
        "mni-Beng",
        "mr",
        "ne",
        "or",
        "pa",
        "sa",
        "sat",
        "sd",
        "sd-Arab",
        "ta",
        "te",
        "ur",
    }
)

# Targets must be speakable for narration (assignment --langs contract).
TARGET_LANGS: frozenset[str] = SPEAK_LANGS

# Declared source language for translate/pivot when --source-lang is set.
SOURCE_LANGS: frozenset[str] = TRANSLATE_LANGS


def parse_lang_list(raw: str) -> list[str]:
    """Split comma-separated language codes and strip blanks."""
    return [part.strip() for part in raw.split(",") if part.strip()]


def validate_target_langs(langs: list[str]) -> None:
    """Raise ValueError if --langs is empty or contains unsupported codes."""
    if not langs:
        raise ValueError("--langs must list at least one language code, e.g. hi,ta,bn")
    unknown = sorted({code for code in langs if code not in TARGET_LANGS})
    if unknown:
        allowed = ", ".join(sorted(TARGET_LANGS))
        raise ValueError(
            f"Unsupported --langs code(s): {', '.join(unknown)}. "
            f"Allowed Indic-Speak codes: {allowed}"
        )


def validate_source_lang(source_lang: str | None) -> None:
    """Raise ValueError if optional --source-lang is set but not allowed."""
    if source_lang is None or source_lang == "":
        return
    if source_lang not in SOURCE_LANGS:
        allowed = ", ".join(sorted(SOURCE_LANGS))
        raise ValueError(
            f"Unsupported --source-lang: {source_lang}. "
            f"Allowed codes: {allowed}"
        )
