"""Format parity, length ratio and reference-based scores."""

from __future__ import annotations

import math
from statistics import median

from sacrebleu.metrics import CHRF

from ..config import Thresholds
from ..models import Category, Severity, Signal
from ..text import markup
from ..text.normalize import norm_text
from ..text.segment import sentences
from .base import ItemContext

_CHRF = CHRF(word_order=2)


def chrf(hypothesis: str, reference: str) -> float:
    return round(_CHRF.sentence_score(norm_text(hypothesis), [norm_text(reference)]).score, 2)


def check_format(ctx: ItemContext) -> None:
    translation = ctx.translation or ""
    with ctx.check("format", "deterministic") as res:
        if not markup.has_markup(ctx.source) and not markup.has_markup(translation):
            res.status = "skipped"
            res.reason = "no Markdown or LaTeX in source or translation"
            return
        issues = markup.compare(ctx.source, translation)
        res.metrics = {"issues": len(issues), "source_math": len(markup.math_segments(ctx.source)), "translation_math": len(markup.math_segments(translation))}
        for issue in issues:
            category = Category.NUMBER if issue.category == "number" else Category.FORMAT
            score = 0.9 if issue.span is not None or issue.source_span is not None else 0.6
            ctx.add(
                Signal(
                    check="format",
                    category=category,
                    score=score,
                    severity=Severity.MAJOR,
                    span=issue.span,
                    source_span=issue.source_span,
                    message=issue.message,
                    data=issue.data,
                    hard=True,
                )
            )


def length_features(source: str, translation: str) -> float | None:
    s, t = len(norm_text(source)), len(norm_text(translation))
    if s < 8 or t == 0:
        return None
    return math.log(t / s)


class LengthModel:
    """Robust per-language distribution of log(len(translation) / len(source)), fitted on the run's own items."""

    PRIOR_SPREAD = 0.25

    def __init__(self, ratios_by_lang: dict[str, list[float]]):
        self.stats: dict[str, tuple[float, float, int]] = {}
        pooled = [r for rs in ratios_by_lang.values() for r in rs]
        for lang, ratios in ratios_by_lang.items():
            if len(ratios) >= 8:
                med = median(ratios)
                mad = median(abs(r - med) for r in ratios) * 1.4826
                self.stats[lang] = (med, max(mad, 0.08), len(ratios))
            elif pooled:
                self.stats[lang] = (median(pooled), self.PRIOR_SPREAD, len(ratios))

    def z(self, lang: str, ratio: float) -> float | None:
        if lang not in self.stats:
            return None
        med, spread, _ = self.stats[lang]
        return (ratio - med) / spread


def check_length(ctx: ItemContext, model: LengthModel, th: Thresholds) -> None:
    translation = ctx.translation or ""
    with ctx.check("length_ratio", "deterministic") as res:
        ratio = length_features(ctx.source, translation)
        if ratio is None:
            res.status = "skipped"
            res.reason = "text too short for a length ratio"
            return
        z = model.z(ctx.lang.code, ratio)
        src_sents, tgt_sents = len(sentences(ctx.source)), len(sentences(translation))
        res.metrics = {
            "log_ratio": round(ratio, 4),
            "z": round(z, 3) if z is not None else None,
            "source_sentences": src_sents,
            "translation_sentences": tgt_sents,
        }
        if z is None:
            return
        if z <= -th.length_z:
            ctx.add(
                Signal(
                    check="length_ratio",
                    category=Category.OMISSION,
                    score=min(0.35 + 0.05 * (-z - th.length_z), 0.6),
                    severity=Severity.MAJOR,
                    message=f"translation is unusually short for {ctx.lang.name} (z={z:.1f})",
                    data={"z": z},
                )
            )
        elif z >= th.length_z:
            ctx.add(
                Signal(
                    check="length_ratio",
                    category=Category.ADDITION,
                    score=min(0.35 + 0.05 * (z - th.length_z), 0.6),
                    severity=Severity.MAJOR,
                    message=f"translation is unusually long for {ctx.lang.name} (z={z:.1f})",
                    data={"z": z},
                )
            )
        if src_sents >= 2 and tgt_sents < src_sents and z < -1.0:
            ctx.add(
                Signal(
                    check="length_ratio",
                    category=Category.OMISSION,
                    score=0.35,
                    severity=Severity.MAJOR,
                    message=f"source has {src_sents} sentences, translation {tgt_sents}",
                    data={"source_sentences": src_sents, "translation_sentences": tgt_sents},
                )
            )
        elif tgt_sents > src_sents >= 1 and z > 1.0:
            ctx.add(
                Signal(
                    check="length_ratio",
                    category=Category.ADDITION,
                    score=0.35,
                    severity=Severity.MAJOR,
                    message=f"translation has {tgt_sents} sentences for {src_sents} in the source",
                    data={"source_sentences": src_sents, "translation_sentences": tgt_sents},
                )
            )


def check_reference(ctx: ItemContext) -> None:
    with ctx.check("reference_chrf", "deterministic") as res:
        if not ctx.item.reference:
            res.status = "skipped"
            res.reason = "no reference translation"
            return
        score = chrf(ctx.translation or "", ctx.item.reference)
        res.metrics = {"chrf++": score}
