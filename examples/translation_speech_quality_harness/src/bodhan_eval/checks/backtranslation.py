"""Back-translation coverage, and the reference-based chrF++ score.

The back-translation (Indic-Translate, target English) is fetched once per item in the pipeline
and stored in ctx.artifacts["backtranslation"]. Back-translation paraphrases, so this check only
reports *strong* coverage gaps (a whole source sentence with almost none of its content words
in the back-translation) and with moderate confidence. It never flags alone.
"""
from __future__ import annotations

import regex
from rapidfuzz import fuzz

from ..text.normalize import normalize, sentences
from .base import Check, Context, Signal, register

STOP = set("""a an the and or but if of to in on at by for with from as is are was were be been being it its this that these those
there here which who whom whose what when where why how not no nor so than then too very can will just also into over under
about after before between during each other some such only own same few more most all any both his her their our your my we
you he she they them him us i do does did has have had having may might must shall should would could one""".split())


def content_words(text: str) -> list[str]:
    words = regex.findall(r"[a-z][a-z'\-]+", text.lower())
    return [stem(w) for w in words if w not in STOP and len(w) > 2]


def stem(w: str) -> str:
    for suf in ("ingly", "edly", "ing", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


def coverage(words: list[str], pool: set[str]) -> float:
    if not words:
        return 1.0
    hit = 0
    for w in words:
        if w in pool or any(fuzz.ratio(w, p) >= 85 for p in pool if abs(len(p) - len(w)) <= 3):
            hit += 1
    return hit / len(words)


@register
class BackTranslationCheck(Check):
    name = "backtranslation"
    tier = 1

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.translation) and bool(ctx.artifacts.get("backtranslation"))

    async def run(self, ctx: Context) -> list[Signal]:
        src, bt, tr = ctx.item.source, ctx.artifacts["backtranslation"], ctx.translation
        bt_pool = set(content_words(bt))
        src_pool = set(content_words(src))
        out: list[Signal] = []
        src_sents = sentences(src)
        for s, e in src_sents:
            words = content_words(src[s:e])
            if len(words) < 3:
                continue
            cov = coverage(words, bt_pool)
            if cov <= 0.34:
                out.append(Signal(self.name, "omission", "major", 0.5, "A source sentence is barely represented in the back-translation.",
                                  None, (s, e), {"coverage": round(cov, 2), "source_sentence": src[s:e][:200]}))
        bt_sents = sentences(bt)
        tr_sents = sentences(tr)
        for k, (s, e) in enumerate(bt_sents):
            words = content_words(bt[s:e])
            if len(words) < 3:
                continue
            cov = coverage(words, src_pool)
            if cov <= 0.2:
                span = tr_sents[k] if len(tr_sents) == len(bt_sents) else None
                out.append(Signal(self.name, "addition", "major", 0.45, "The back-translation contains a sentence with no counterpart in the source.",
                                  span, None, {"coverage": round(cov, 2), "bt_sentence": bt[s:e][:200]}))
        return out


@register
class ReferenceCheck(Check):
    """chrF++ against a reference translation, when one is supplied. Reported as a metric;
    only a very low score becomes a (weak) signal, because a valid translation can differ a lot."""
    name = "reference"
    tier = 0

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.translation) and bool(ctx.item.reference)

    async def run(self, ctx: Context) -> list[Signal]:
        from sacrebleu.metrics import CHRF

        hyp = normalize(ctx.translation, "strict").text
        ref = normalize(ctx.item.reference, "strict").text
        score = CHRF(word_order=2).sentence_score(hyp, [ref]).score
        ctx.artifacts["chrf_pp"] = round(score, 2)
        if score < 25:
            return [Signal(self.name, "mistranslation", "major", 0.35, f"Very low chrF++ against the reference ({score:.1f}).", None, None,
                           {"chrf_pp": round(score, 2)})]
        return []
