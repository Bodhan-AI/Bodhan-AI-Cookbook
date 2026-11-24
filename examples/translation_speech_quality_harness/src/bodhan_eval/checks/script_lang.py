"""Wrong language or script, and untranslated text.

Counts letters by Unicode script after removing math, code, URLs and Latin words that also
appear in the source (names and acronyms are legitimately kept in Latin script).
"""
from __future__ import annotations

import unicodedata
from collections import Counter

import regex

from ..langs import LANGS
from ..text.markup import INLINE_CODE, URL, math_segments
from ..text.normalize import script_of, sentences
from .base import Check, Context, Signal, register
from .entities import source_entities

LATIN_WORD = regex.compile(r"[A-Za-z][A-Za-z'\-]*")


def _masked(text: str, source_words: set[str]) -> str:
    chars = list(text)
    spans = [(s, e) for s, e, _ in math_segments(text)]
    spans += [(m.start(), m.end()) for rx in (INLINE_CODE, URL) for m in rx.finditer(text)]
    spans += [(m.start(), m.end()) for m in LATIN_WORD.finditer(text) if m.group(0).lower() in source_words]
    for s, e in spans:
        chars[s:e] = [" "] * (e - s)
    return "".join(chars)


def script_counts(text: str) -> Counter:
    # letters AND combining marks: Indic vowel signs are marks (Mn/Mc), not letters
    return Counter(script_of(ch) for ch in text if ch.isalpha() or unicodedata.category(ch) in ("Mn", "Mc"))


@register
class ScriptLanguageCheck(Check):
    name = "script_lang"
    tier = 0

    def applies(self, ctx: Context) -> bool:
        return ctx.lang.script not in ("Unknown", "Latin")

    async def run(self, ctx: Context) -> list[Signal]:
        tr, src = ctx.translation, ctx.item.source
        if not tr.strip():
            return [Signal(self.name, "omission", "critical", 0.99, "Translation is empty.", None, (0, len(src)))]
        # Only names, acronyms and alphanumeric terms may legitimately stay in Latin script;
        # ordinary English words do not get this exemption (that would hide untranslated sentences).
        source_words = {w.lower() for ent, _, _ in source_entities(src) for w in LATIN_WORD.findall(ent)} - _COMMON_EN
        masked = _masked(tr, source_words)
        counts = script_counts(masked)
        total = sum(counts.values())
        if total == 0:
            return []
        expected = counts.get(ctx.lang.script, 0) / total
        out: list[Signal] = []
        # sentence level first: which sentences are in the wrong script?
        bad, good = [], 0
        for s, e in sentences(tr):
            sc = script_counts(masked[s:e])
            n = sum(sc.values())
            if n < 12:
                continue
            dom, cnt = sc.most_common(1)[0]
            if dom != ctx.lang.script and cnt / n > 0.7:
                bad.append((s, e, dom, cnt / n))
            elif sc.get(ctx.lang.script, 0) / n >= 0.5:
                good += 1
        if (bad and not good) or (expected < 0.5 and not good):
            dom, _ = counts.most_common(1)[0]
            what = "left untranslated (English)" if dom == "Latin" else f"in {dom} script instead of {ctx.lang.script}"
            return [Signal(self.name, "wrong_language", "critical", 0.95, f"The translation is {what}.", (0, len(tr)), None,
                           {"script_share": {k: round(v / total, 3) for k, v in counts.most_common()}})]
        for s, e, dom, share in bad:
            kind = "untranslated English" if dom == "Latin" else f"{dom} script"
            out.append(Signal(self.name, "wrong_language", "major", 0.9, f"A sentence is {kind}.", (s, e), None,
                              {"sentence_script": dom, "share": round(share, 3)}))
        # same-script languages (hi/mr/ne, bn/as): function-word fingerprints
        if ctx.lang.fingerprint:
            out += self._same_script_language(ctx, masked, tr)
        return out

    def _same_script_language(self, ctx: Context, masked: str, tr: str) -> list[Signal]:
        """hi vs mr vs ne, bn vs as: compare function-word and marker-character evidence."""
        words = regex.findall(r"[\p{L}\p{M}]+", masked)

        def score(lang, exclude=frozenset()):
            markers = lang.markers - exclude
            return (sum(w in lang.fingerprint for w in words)
                    + sum(1 for w in words if markers and any(c in w for c in markers))
                    + sum(1 for w in words if len(w) > 3 and w.endswith(lang.suffixes)))

        own = score(ctx.lang)
        for other in LANGS.values():
            if other.code == ctx.lang.code or other.script != ctx.lang.script or not (other.fingerprint or other.markers):
                continue
            theirs = score(other, exclude=ctx.lang.markers)
            dominant = theirs >= 3 and theirs > 2 * max(own, 1)
            absent = len(words) >= 8 and own == 0 and theirs >= 2
            if dominant or absent:
                return [Signal(self.name, "wrong_language", "critical", 0.85 if dominant else 0.8,
                               f"The text reads as {other.name}, not {ctx.lang.name} (function words / characters).", (0, len(tr)), None,
                               {"evidence": {ctx.lang.code: own, other.code: theirs}, "words": len(words)})]
        return []


_COMMON_EN = set("the a an and or of to in on at is are was were be it this that for with as by from".split())
