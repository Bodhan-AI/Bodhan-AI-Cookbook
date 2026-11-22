"""Glossary (--glossary glossary.yaml): fixed target-language renderings for English terms.

Indic-Translate has no glossary field and drops placeholder markers, so a rendering cannot be forced. The
pipeline (1) checks every translation for each glossary term the English contains, (2) re-translates a unit
once with the rendering already written into the English source, keeping that retry only if it helps, and
(3) reports every term still missing. Measured 2026-11-20 (hi/ta/bn): when Indic-Translate's own word differed
from the wanted one, the retry kept the wanted rendering in 2 of 5 cases, so it is a best effort.

Accepted file shapes (anything else is skipped with a warning; only a missing file stops the run):
    term-first      angle: {hi: कोण, ta: கோணம்}
    language-first  hi: {angle: कोण}
    entry list      - {term: angle, hi: ..., ta: ...}   (or translations: {hi: ...})
    same in all     NASA: NASA            (one rendering for every language)
    keep as is      - NASA                (a plain term: keep it unchanged in every language)
An optional top-level wrapper key "glossary", "terms" or "entries" is unwrapped.

Matching. English: whole words, case-insensitive (acronyms such as ATP case-sensitive), plurals -s/-es/-ies.
Target: Indian languages add case endings to a noun and may change its last letter (Tamil கோணம் "angle",
plural கோணங்கள்), so a translation word matches when it starts with the rendering's stem: the rendering
without its final vowel signs and, for words of 3+ letters, without its last letter. On the demo's Tamil
track an exact match missed 14 of 26 uses of கோணம்; the stem rule finds all 26.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

ALL = "*"                     # rendering used for every language
WRAPPERS = ("glossary", "terms", "entries")
TERM_KEYS = ("term", "en", "english", "source")


# --------------------------------------------------------------------------- loading
def load_glossary(path: str | Path) -> dict[str, dict[str, str]]:
    """{english term: {lang or '*': rendering}}. Missing file -> FileNotFoundError (before any paid call).
    A file that cannot be read as YAML gives a warning and an empty glossary, so the run goes on without it."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"glossary file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        log.warning("glossary %s could not be read (%s); running without a glossary", p, str(e).splitlines()[0])
        return {}
    g = parse_glossary(data, str(p))
    log.info("glossary %s: %d terms", p, len(g))
    return g


def parse_glossary(data, source: str = "glossary") -> dict[str, dict[str, str]]:
    # imported here, not at the top: translate imports this module
    from .translate import LANG_NAMES

    out: dict[str, dict[str, str]] = {}
    skipped: list[str] = []

    def add(term, lang, rendering) -> None:
        term = str(term).strip() if term is not None else ""
        lang = str(lang).strip().lower()
        if rendering is None or isinstance(rendering, (dict, list)) or not term:
            skipped.append(f"{term or '?'}/{lang}")
            return
        rendering = str(rendering).strip()
        if lang != ALL and lang not in LANG_NAMES:
            skipped.append(f"{term}/{lang} (unknown language)")
            return
        if rendering:
            out.setdefault(term, {})[lang] = rendering

    if isinstance(data, dict) and len(data) == 1 and str(next(iter(data))).lower() in WRAPPERS:
        data = next(iter(data.values()))
    if data is None:
        log.warning("%s is empty; running without a glossary", source)
        return {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, (str, int, float)):
                add(item, ALL, item)                                   # keep as is
            elif isinstance(item, dict):
                term = next((item[k] for k in TERM_KEYS if item.get(k)), None)
                tr = item.get("translations") if isinstance(item.get("translations"), dict) else \
                    {k: v for k, v in item.items() if str(k).lower() not in TERM_KEYS}
                if term is None or not tr:
                    skipped.append(str(item)[:40])
                    continue
                for lang, r in tr.items():
                    add(term, lang, r)
            else:
                skipped.append(str(item)[:40])
    elif isinstance(data, dict):
        langs = set(LANG_NAMES)
        if all(str(k).lower() in langs for k in data) and all(isinstance(v, dict) for v in data.values()):
            for lang, terms in data.items():                            # language-first
                for term, r in terms.items():
                    add(term, lang, r)
        else:
            for term, v in data.items():                                # term-first
                if isinstance(v, dict):
                    for lang, r in v.items():
                        add(term, lang, r)
                elif v is None:
                    add(term, ALL, term)
                else:
                    add(term, ALL, v)
    else:
        log.warning("%s: expected a mapping or a list of terms, got %s; running without a glossary",
                    source, type(data).__name__)
        return {}
    if skipped:
        log.warning("%s: skipped %d entries it could not use: %s", source, len(skipped), ", ".join(skipped[:10]))
    return out


def renderings_for(glossary: dict | None, lang: str) -> dict[str, str]:
    """{term: rendering} for one language (a language-specific rendering wins over '*')."""
    out = {}
    for term, tr in (glossary or {}).items():
        r = tr.get(lang) or tr.get(ALL)
        if r:
            out[term] = r
    return out


# --------------------------------------------------------------------------- matching
@lru_cache(maxsize=4096)
def _en_pattern(term: str) -> re.Pattern:
    t = term.strip()
    words = [re.escape(w) for w in t.split()]
    alts = [r"\s+".join(words) + r"(?:s|es)?"]
    if t[-1:].lower() == "y" and len(t) > 2:
        alts.append(r"\s+".join(words[:-1] + [re.escape(t.split()[-1][:-1])]) + "ies")
    acronym = t.isupper() and len(t.replace(" ", "")) >= 2
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", 0 if acronym else re.I)


def match_en(term: str, text: str) -> bool:
    return bool(term.strip()) and bool(_en_pattern(term).search(text))


def _words(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", text).lower()
    t = "".join(c for c in t if unicodedata.category(c) != "Cf")          # zero-width joiners
    t = "".join(" " if unicodedata.category(c)[0] in "PSZ" else c for c in t)
    return t.split()


def _stem(word: str) -> str:
    if word.isascii():
        return word
    s = word
    while s and unicodedata.category(s[-1])[0] == "M":                    # final vowel signs, virama, nukta
        s = s[:-1]
    if sum(unicodedata.category(c)[0] == "L" for c in s) >= 3:
        s = s[:-1]
    return s or word


def match_target(rendering: str, text: str) -> bool:
    """True if the translation contains the rendering (each word may carry an ending, see the module doc)."""
    stems = [_stem(w) for w in _words(rendering)]
    tw = _words(text)
    if not stems:
        return False
    return any(all(tw[i + k].startswith(s) for k, s in enumerate(stems)) for i in range(len(tw) - len(stems) + 1))


def missing_terms(en: str, text: str, glossary: dict | None, lang: str) -> list[str]:
    """Glossary terms that the English contains but the translation lacks."""
    return [t for t, r in renderings_for(glossary, lang).items() if match_en(t, en) and not match_target(r, text)]


def force_terms(en: str, renderings: dict[str, str]) -> str:
    """The English with each term replaced by its rendering, for the one re-translation that tries to keep it.
    Latin renderings are left alone: the English already contains them."""
    out = en
    for term, r in renderings.items():
        if not r.isascii():
            out = _en_pattern(term).sub(lambda _m, r=r: r, out)
    return out
