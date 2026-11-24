"""Language, voice and script knowledge, loaded from Bodhan's published API rules.

``data/bodhan_api_rules.json`` is vendored verbatim from the Cookbook
(``scripts/bodhan_api_rules.json``) so supported languages and voices come from
Bodhan's source of truth rather than being hand-maintained here.

Script detection is the only language identification done in plain code; it is
explicitly allowed ("language-identification by script or character set").
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from importlib import resources
from typing import Any

from . import errors

CAPABILITY_MODEL = {
    "transcribe": "indic-transcribe",
    "translate": "indic-translate",
    "speak": "indic-speak",
    "ocr": "indic-ocr",
}


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    text = resources.files("bodhan_mcp").joinpath("data/bodhan_api_rules.json").read_text(encoding="utf-8")
    return json.loads(text)


def language_names() -> dict[str, str]:
    return dict(rules()["language_names"])


def name_of(code: str) -> str:
    return language_names().get(code, code)


def supported(capability: str) -> list[str]:
    """Language codes supported by a capability (transcribe, translate, speak). OCR is script-agnostic."""
    model = CAPABILITY_MODEL[capability]
    return list(rules()["models"][model].get("languages", []))


def voices() -> dict[str, dict[str, list[str]]]:
    return dict(rules()["models"]["indic-speak"]["voices"])


def styles() -> list[str]:
    return list(rules()["models"]["indic-speak"]["styles"])


def require_language(code: str, capability: str) -> str:
    """Validate and normalise a language code for a capability, or raise an actionable error."""
    raw = (code or "").strip()
    norm = resolve_language(raw) or raw
    allowed = supported(capability)
    if norm not in allowed:
        hint = f"Supported for {capability}: {', '.join(allowed)}."
        if norm.endswith("-IN"):
            hint = f"Use ISO codes like '{norm[:-3]}', not '{norm}'. " + hint
        raise errors.BodhanError(
            errors.UNSUPPORTED_LANGUAGE,
            f"Language {raw!r} is not supported for {capability} by Bodhan models.",
            hint=hint,
            details={"language": raw, "capability": capability, "supported": allowed},
        )
    return norm


def default_voice(lang: str, gender: str | None = None) -> str:
    table = voices().get(lang)
    if not table:
        raise errors.BodhanError(
            errors.UNKNOWN_VOICE,
            f"No Indic-Speak voice is listed for language {lang!r}.",
            hint=f"Languages with voices: {', '.join(sorted(voices()))}.",
        )
    g = (gender or "female").lower()
    if g not in table:
        g = "female" if "female" in table else next(iter(table))
    return table[g][0]


def validate_voice(lang: str, voice: str) -> str:
    table = voices().get(lang, {})
    for names in table.values():
        for name in names:
            if name.lower() == voice.strip().lower():
                return name
    options = [n for names in table.values() for n in names]
    raise errors.BodhanError(
        errors.UNKNOWN_VOICE,
        f"Voice {voice!r} is not available for language {lang!r}.",
        hint=f"Voices for {lang}: {', '.join(options) or 'none'}. Or pass gender='female'|'male' instead of a voice.",
        details={"language": lang, "voices": table},
    )


def gender_of_voice(lang: str, voice: str) -> str | None:
    for gender, names in voices().get(lang, {}).items():
        if voice in names:
            return gender
    return None


# --------------------------------------------------------------------------------------
# Language names -> codes (used to read task text and to accept "Tamil" as well as "ta")
# --------------------------------------------------------------------------------------

_EXTRA_ALIASES = {
    "bangla": "bn", "oriya": "or", "kashmiri": "ks", "manipuri": "mni", "meitei": "mni", "sindhi": "sd",
    "punjabi": "pa", "panjabi": "pa", "hindustani": "hi", "konkani": "kok", "bodo": "brx", "dogri": "doi",
    "santhali": "sat", "santali": "sat", "maithili": "mai", "sanskrit": "sa", "bhojpuri": "bho", "bhili": "bhb",
    "english": "en", "assamese": "as", "nepali": "ne", "urdu": "ur", "hinglish": "hi", "tanglish": "ta",
}

# World languages Bodhan does not support. Used only to recognise them in task text so the agent
# can fail honestly ("French is not supported") instead of guessing.
UNSUPPORTED_WORLD_LANGUAGES = {
    "french", "german", "spanish", "italian", "portuguese", "russian", "chinese", "mandarin", "cantonese",
    "japanese", "korean", "arabic", "persian", "farsi", "turkish", "dutch", "swedish", "norwegian", "danish",
    "finnish", "polish", "czech", "greek", "hebrew", "thai", "vietnamese", "indonesian", "malay", "swahili",
    "ukrainian", "romanian", "hungarian", "tagalog", "filipino", "sinhala", "sinhalese", "burmese", "khmer",
    "lao", "tibetan", "pashto", "amharic", "yoruba", "zulu", "latin", "esperanto",
}


@lru_cache(maxsize=1)
def _name_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for code, name in language_names().items():
        base = name.split("(")[0].strip().lower()
        idx.setdefault(base, code)
        idx[name.lower()] = code
    idx.update(_EXTRA_ALIASES)
    return idx


def resolve_language(value: str | None) -> str | None:
    """Accept a code ('ta'), a BCP-47 region tag (ta-IN) or a name ('Tamil'); return the Bodhan code or None."""
    if not value:
        return None
    v = value.strip()
    codes = set(language_names())
    if v in codes:
        return v
    if v.lower() in codes:
        return v.lower()
    if re.fullmatch(r"[a-z]{2,3}-IN", v, flags=re.IGNORECASE) and v[:-3].lower() in codes:
        return v[:-3].lower()
    return _name_index().get(v.lower())


def mentioned_languages(text: str) -> dict[str, list[str]]:
    """Find language names in free text. Returns {'supported': [codes], 'unsupported': [names]}."""
    words = re.findall(r"[A-Za-z][A-Za-z-]+", text.lower())
    found_codes: list[str] = []
    unsupported: list[str] = []
    idx = _name_index()
    for w in words:
        if w in idx and idx[w] not in found_codes:
            found_codes.append(idx[w])
        elif w in UNSUPPORTED_WORLD_LANGUAGES and w not in unsupported:
            unsupported.append(w)
    return {"supported": found_codes, "unsupported": unsupported}


# --------------------------------------------------------------------------------------
# Script detection
# --------------------------------------------------------------------------------------

SCRIPT_RANGES: list[tuple[str, int, int]] = [
    ("Devanagari", 0x0900, 0x097F),
    ("Devanagari", 0xA8E0, 0xA8FF),
    ("Bengali", 0x0980, 0x09FF),
    ("Gurmukhi", 0x0A00, 0x0A7F),
    ("Gujarati", 0x0A80, 0x0AFF),
    ("Oriya", 0x0B00, 0x0B7F),
    ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F),
    ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F),
    ("OlChiki", 0x1C50, 0x1C7F),
    ("MeeteiMayek", 0xABC0, 0xABFF),
    ("MeeteiMayek", 0xAAE0, 0xAAFF),
    ("Arabic", 0x0600, 0x06FF),
    ("Arabic", 0x0750, 0x077F),
    ("Arabic", 0xFB50, 0xFDFF),
    ("Arabic", 0xFE70, 0xFEFF),
]

SCRIPT_LANGUAGES: dict[str, list[str]] = {
    "Devanagari": ["hi", "mr", "ne", "mai", "sa", "kok", "doi", "brx", "bho", "sd", "ks-Deva"],
    "Bengali": ["bn", "as", "mni-Beng"],
    "Gurmukhi": ["pa"],
    "Gujarati": ["gu"],
    "Oriya": ["or"],
    "Tamil": ["ta"],
    "Telugu": ["te"],
    "Kannada": ["kn"],
    "Malayalam": ["ml"],
    "OlChiki": ["sat"],
    "MeeteiMayek": ["mni"],
    "Arabic": ["ur", "ks", "sd-Arab"],
    "Latin": ["en"],
}

LANGUAGE_SCRIPT: dict[str, str] = {}
for _script, _langs in SCRIPT_LANGUAGES.items():
    for _l in _langs:
        LANGUAGE_SCRIPT.setdefault(_l, _script)

INDIC_SCRIPTS = {s for s in SCRIPT_LANGUAGES if s not in {"Latin"}}


def char_script(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    if ch.isascii() and ch.isalpha():
        return "Latin"
    if ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN"):
        return "Latin"
    return None


def script_profile(text: str) -> dict[str, float]:
    """Fraction of letters per script, ignoring digits, punctuation and whitespace."""
    counts: Counter[str] = Counter()
    for ch in text:
        s = char_script(ch)
        if s:
            counts[s] += 1
    total = sum(counts.values())
    return {k: round(v / total, 4) for k, v in counts.most_common()} if total else {}


def detect_script(text: str) -> str | None:
    prof = script_profile(text)
    return next(iter(prof), None)


def language_candidates(text: str) -> list[str]:
    """Likely language codes for a text, most likely first, from its dominant script."""
    script = detect_script(text)
    if not script:
        return []
    cands = list(SCRIPT_LANGUAGES.get(script, []))
    # Assamese uses ৰ / ৱ, which Bengali does not.
    if script == "Bengali" and re.search("[ৰৱ]", text):
        cands.remove("as")
        cands.insert(0, "as")
    # Marathi's frequent ळ and Nepali/Marathi conventions are not reliable enough to reorder Devanagari.
    return cands


def indic_fraction(text: str) -> float:
    prof = script_profile(text)
    return round(sum(v for k, v in prof.items() if k in INDIC_SCRIPTS), 4)


def contains_indic(text: str, min_chars: int = 1) -> bool:
    n = 0
    for ch in text:
        s = char_script(ch)
        if s and s in INDIC_SCRIPTS:
            n += 1
            if n >= min_chars:
                return True
    return False


def expected_script(lang: str, script: str = "native") -> str:
    if script in {"roman", "codemix"}:
        return "Latin"
    return LANGUAGE_SCRIPT.get(lang, "Latin")
