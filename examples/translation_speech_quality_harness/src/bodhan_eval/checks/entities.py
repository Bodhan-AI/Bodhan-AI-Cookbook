"""Names, acronyms and technical terms must survive translation.

An entity from the source counts as preserved if it appears verbatim, OR its consonant skeleton
matches a romanized window of the translation, OR the back-translation contains it. Two
independent views (transliteration and back-translation) must both miss it before we suspect
an error, which keeps checker noise out.
"""
from __future__ import annotations

import regex
from rapidfuzz import fuzz

from .base import Check, Context, Signal, register

try:
    from indic_transliteration import sanscript
    from indic_transliteration.sanscript import transliterate
except ImportError:  # pragma: no cover
    sanscript = None

SCHEME = {"Devanagari": "DEVANAGARI", "Bengali": "BENGALI", "Tamil": "TAMIL", "Telugu": "TELUGU", "Kannada": "KANNADA",
          "Malayalam": "MALAYALAM", "Gujarati": "GUJARATI", "Gurmukhi": "GURMUKHI", "Oriya": "ORIYA"}

SKIP = set("I A The This That These Those It In On At For From With By Of And Or But If When While As To Is Are Was Were Be We You He She They "
           "My Our Your His Her Their Its Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June "
           "July August September October November December".split())


def source_entities(text: str) -> list[tuple[str, int, int]]:
    ents = []
    for m in regex.finditer(r"\b(?:[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*|[A-Z]{2,}[0-9]*|[A-Za-z]+[0-9]+[A-Za-z0-9]*)\b", text):
        w = m.group(0)
        start = m.start()
        sentence_initial = start == 0 or regex.search(r"[.!?:\n]\s*$", text[:start]) is not None
        words = w.split()
        if len(words) == 1 and (w in SKIP or (sentence_initial and not w.isupper() and not any(c.isdigit() for c in w))):
            continue
        if words[0] in SKIP and len(words) > 1:
            off = len(words[0]) + 1
            w, start = w[off:], start + off
        ents.append((w, start, start + len(w)))
    return ents


def skeleton(s: str) -> str:
    s = regex.sub(r"[^a-z]", "", s.lower())
    s = s.replace("ph", "f").replace("th", "t").replace("sh", "s").replace("ch", "c").replace("w", "v").replace("z", "j")
    s = regex.sub(r"[aeiouyh]", "", s)
    return regex.sub(r"(.)\1+", r"\1", s)


def romanize(text: str, script: str) -> str:
    if sanscript is None or script not in SCHEME:
        return ""
    try:
        return transliterate(text, getattr(sanscript, SCHEME[script]), sanscript.ITRANS)
    except Exception:  # noqa: BLE001 - romanization is best-effort evidence
        return ""


def best_window_score(entity: str, roman_tokens: list[str]) -> float:
    target = skeleton(entity)
    if len(target) < 2:
        return 100.0
    n = len(entity.split())
    best = 0.0
    for size in {n, n + 1, max(1, n - 1)}:
        for i in range(len(roman_tokens) - size + 1):
            cand = skeleton("".join(roman_tokens[i:i + size]))
            if cand:
                best = max(best, fuzz.ratio(target, cand))
    return best


@register
class EntityCheck(Check):
    name = "entities"
    tier = 0

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.translation) and bool(source_entities(ctx.item.source))

    async def run(self, ctx: Context) -> list[Signal]:
        tr = ctx.translation
        bt = (ctx.artifacts.get("backtranslation") or "").lower()
        roman = romanize(tr, ctx.lang.script)
        roman_tokens = regex.findall(r"[A-Za-z~^.']+", roman)
        latin_in_tr = tr.lower()
        out = []
        for ent, s, e in source_entities(ctx.item.source):
            el = ent.lower()
            if el in latin_in_tr:
                continue
            translit = best_window_score(ent, roman_tokens) if roman_tokens else 0.0
            in_bt = bool(bt) and (el in bt or fuzz.partial_ratio(el, bt) >= 88)
            if translit >= 70 or in_bt:
                continue
            conf = 0.6 if bt else 0.4
            out.append(Signal(self.name, "term", "major", conf, f"Name/term {ent!r} is not recognisable in the translation or its back-translation.",
                              None, (s, e), {"entity": ent, "transliteration_match": round(translit, 1), "in_back_translation": in_bt}))
        return out
