"""Which script each language is written in, and the script of any character.

Scripts come from Unicode character names, so detection works for every script with no per-script
code. A language missing from the table gets the script most of its translations in the dataset use.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable

# Accepted scripts per language. Codes Bodhan pairs with a script variant (ks / ks-Deva, sd / sd-Arab,
# mni / mni-Beng) are ambiguous on their own, so they accept both rather than risk flagging every item.
SCRIPTS: dict[str, set[str]] = {
    **{code: {"DEVANAGARI"} for code in ("hi", "mr", "ne", "sa", "kok", "mai", "doi", "brx")},
    "bn": {"BENGALI"}, "as": {"BENGALI"},  # Unicode names the Assamese letters BENGALI too
    "pa": {"GURMUKHI"}, "gu": {"GUJARATI"}, "or": {"ORIYA"}, "ta": {"TAMIL"}, "te": {"TELUGU"},
    "kn": {"KANNADA"}, "ml": {"MALAYALAM"}, "ur": {"ARABIC"}, "sat": {"OL CHIKI"}, "en": {"LATIN"},
    "ks": {"ARABIC", "DEVANAGARI"}, "sd": {"ARABIC", "DEVANAGARI"}, "mni": {"MEETEI MAYEK", "BENGALI"},
}
# ISO 15924 script subtags, as in mni-Beng or ks-Deva: the subtag decides.
SUBTAGS = {"Deva": "DEVANAGARI", "Beng": "BENGALI", "Arab": "ARABIC", "Latn": "LATIN", "Mtei": "MEETEI MAYEK",
           "Olck": "OL CHIKI", "Guru": "GURMUKHI", "Gujr": "GUJARATI", "Orya": "ORIYA", "Taml": "TAMIL",
           "Telu": "TELUGU", "Knda": "KANNADA", "Mlym": "MALAYALAM"}
_MULTI_WORD = ("MEETEI MAYEK", "OL CHIKI")


def script_of(ch: str) -> str | None:
    """Script name of a letter or combining mark ('DEVANAGARI', 'LATIN', ...); None for anything else."""
    if not unicodedata.category(ch)[0] in "LM":
        return None
    name = unicodedata.name(ch, "")
    for s in _MULTI_WORD:
        if name.startswith(s):
            return s
    return name.split(" ", 1)[0] or None


def script_counts(text: str) -> Counter:
    return Counter(s for s in map(script_of, text) if s)


def expected_scripts(lang: str, translations: Iterable[str] = ()) -> set[str] | None:
    """Scripts a translation into `lang` should use. Unknown codes: the dataset's majority script."""
    base, _, subtag = lang.partition("-")
    if subtag in SUBTAGS:
        return {SUBTAGS[subtag]}
    if base in SCRIPTS:
        return SCRIPTS[base]
    votes = Counter()
    for t in translations:
        counts = script_counts(t)
        counts.pop("LATIN", None)  # Latin names and terms are common in any language
        if counts:
            votes[counts.most_common(1)[0][0]] += 1
    return {votes.most_common(1)[0][0]} if votes else None
