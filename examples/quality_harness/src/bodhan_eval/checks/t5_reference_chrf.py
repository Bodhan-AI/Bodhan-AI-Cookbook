"""T5 reference_chrf: when a reference translation exists, score the system translation
against it with chrF++ and localize the biggest divergence.

Marked NOISY: references can themselves be imperfect or legitimately paraphrase, so a low
chrF alone must not flag; it needs corroboration (another check or the judge).
"""

from __future__ import annotations

from ..align import diff_original
from ..models import Evidence
from ..normalize import normalize
from ..text_utils import chrf
from .registry import CheckContext, register


@register("t5_reference_chrf", category="mistranslation",
          description="chrF++ vs reference translation (when present)")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    ref = ctx.reference
    if not tr or not ref:
        return []
    try:
        score = chrf(tr, ref)
    except Exception:  # sacrebleu optional; never fatal
        return []
    cut = ctx.thresholds.get("reference_chrf_cut", ctx.lang)
    if score >= cut:
        return []
    # localize: biggest diff region between translation and reference
    tr_n, ref_n = normalize(tr, ctx.lang), normalize(ref, ctx.lang)
    ops = diff_original(ref_n, tr_n)
    worst = max(ops, key=lambda o: o.b_span[1] - o.b_span[0], default=None)
    target_span = worst.b_span if worst else None
    return [Evidence(
        check="t5_reference_chrf", category="mistranslation", strength="medium", noisy=True,
        score=(cut - score) / max(cut, 1.0), severity_hint="major", target_span=target_span,
        details={"chrf": round(score, 2), "cut": cut},
    )]
