"""Supported languages and voices per model, and a free script-based language guess.

Tables come from bodhan_api_rules.json (a copy of the Cookbook's scripts/bodhan_api_rules.json).
"""

from __future__ import annotations

import json
from pathlib import Path

_RULES = json.loads(Path(__file__).with_name("bodhan_api_rules.json").read_text(encoding="utf-8"))
_M = _RULES["models"]

STT_LANGS = frozenset(_M["indic-transcribe"]["languages"])
TRANSLATE_LANGS = frozenset(_M["indic-translate"]["languages"])
TTS_LANGS = frozenset(_M["indic-speak"]["languages"])
VOICES: dict[str, dict[str, list[str]]] = _M["indic-speak"]["voices"]  # lang -> {"female": [...], "male": [...]}
ALL_VOICES = {v for by_gender in VOICES.values() for names in by_gender.values() for v in names}
STYLES = frozenset(_M["indic-speak"]["styles"])
NAMES: dict[str, str] = _RULES["language_names"]  # code -> English name
SUPPORTED = {"transcribe": STT_LANGS, "translate": TRANSLATE_LANGS, "speak": TTS_LANGS}

_BY_NAME = {name.split(" (")[0].lower(): code for code, name in reversed(NAMES.items())}  # first code wins
_BY_NAME |= {"oriya": "or", "bangla": "bn"}


def normalize_lang(value: str | None) -> str | None:
    """Tamil / ta / TA / ta-IN -> ta. Unknown (French, fr) -> None."""
    v = (value or "").strip().lower()
    for code in NAMES:
        if v == code.lower():
            return code
    return v.split("-")[0] if v.split("-")[0] in NAMES else _BY_NAME.get(v)


def pick_voice(lang: str, gender: str = "female") -> str:
    by_gender = VOICES.get(lang, VOICES["hi"])
    return (by_gender.get(gender) or by_gender["female"])[0]


# Unicode block -> languages written in it (first one is the default guess).
_SCRIPTS = [
    (0x0900, 0x097F, ("hi", "mr", "ne", "sa", "mai", "kok", "doi", "brx", "sd", "ks-Deva", "bho", "bhb")),
    (0x0980, 0x09FF, ("bn", "as", "mni-Beng")),
    (0x0A00, 0x0A7F, ("pa",)),
    (0x0A80, 0x0AFF, ("gu",)),
    (0x0B00, 0x0B7F, ("or",)),
    (0x0B80, 0x0BFF, ("ta",)),
    (0x0C00, 0x0C7F, ("te",)),
    (0x0C80, 0x0CFF, ("kn",)),
    (0x0D00, 0x0D7F, ("ml",)),
    (0x0600, 0x06FF, ("ur", "ks", "sd-Arab")),
    (0x1C50, 0x1C7F, ("sat",)),
    (0xABC0, 0xABFF, ("mni",)),
    (0x0041, 0x007A, ("en",)),
]


def detect_script(text: str) -> tuple[str | None, tuple[str, ...]]:
    """Return (best guess, all candidate languages) from the dominant script. Free and instant,
    but scripts are shared (Hindi/Marathi/Nepali are all Devanagari): >1 candidate = ambiguous."""
    counts: dict[tuple[str, ...], int] = {}
    for ch in text:
        if ch.isalpha():
            for lo, hi, langs in _SCRIPTS:
                if lo <= ord(ch) <= hi:
                    counts[langs] = counts.get(langs, 0) + 1
                    break
    if not counts:
        return None, ()
    langs = max(counts, key=counts.__getitem__)
    if langs[0] == "bn" and {"ৰ", "ৱ"} & set(text):  # ৰ ৱ exist only in Assamese
        return "as", langs
    return langs[0], langs
