"""Reference-based score: chrF++ per sentence against a human reference, when one exists.

This never produces flags. A low-scoring sentence is a *signal* that routes attention
(to the judge, to the report) and backs up flags from other checks: a correct translation
can legitimately differ from a reference, so a low score alone is not an error.
"""

from __future__ import annotations

from functools import lru_cache

from sacrebleu.metrics import CHRF

from bodhan_eval.align import align_segments
from bodhan_eval.checks.base import CheckResult
from bodhan_eval.normalize import split_sentences, strip_invisible, to_ascii_digits
from bodhan_eval.params import Params


@lru_cache(maxsize=1)
def _chrf_pp() -> CHRF:
    return CHRF(word_order=2)  # chrF++ (recall-weighted, beta=2): the scoring metric


@lru_cache(maxsize=1)
def _chrf_sym() -> CHRF:
    return CHRF(word_order=2, beta=1)  # symmetric F1 variant: used only to align segments


def _prep(t: str) -> str:
    # Digit systems (१२ vs 12) and invisible joiners are not translation quality.
    return to_ascii_digits(strip_invisible(t))


def chrf_pp(hyp: str, ref: str) -> float:
    return _chrf_pp().sentence_score(_prep(hyp), [_prep(ref)]).score


def similarity(a: str, b: str) -> float:
    """Symmetric chrF++ (0..100) for aligning two texts in the same language."""
    if not a.strip() and not b.strip():
        return 100.0
    return _chrf_sym().sentence_score(_prep(a), [_prep(b)]).score


def check_reference(translation: str, reference: str | None, params: Params) -> CheckResult:
    name = "reference"
    if not reference or not reference.strip():
        return CheckResult.skipped(name, "no reference")
    if not translation.strip():
        return CheckResult.skipped(name, "empty translation")
    low = params.get("reference", "low_sentence_chrf", 30.0)
    align_min = params.get("reference", "align_min_chrf", 10.0)

    t_sents, r_sents = split_sentences(translation), split_sentences(reference)
    t_txt = [translation[s:e] for s, e in t_sents]
    r_txt = [reference[s:e] for s, e in r_sents]
    pairs = align_segments(t_txt, r_txt, similarity, min_sim=align_min)

    sentences = []
    for p in pairs:
        if not p.a:
            continue  # reference-only sentence: the omission side is the other checks' job
        span = (t_sents[p.a[0]][0], t_sents[p.a[-1]][1])
        if p.b:
            ref = " ".join(r_txt[k] for k in p.b)
            score = chrf_pp(translation[span[0] : span[1]], ref)
        else:
            score = 0.0  # no counterpart in the reference at all
        sentences.append({"span": list(span), "chrf": round(score, 2), "aligned": bool(p.b)})

    return CheckResult(
        name,
        signals={
            "chrf": round(chrf_pp(translation, reference), 2),
            "sentences": sentences,
            "low_sentences": [s["span"] for s in sentences if s["chrf"] < low],
            "unaligned_reference_sentences": sum(1 for p in pairs if not p.a),
        },
    )
