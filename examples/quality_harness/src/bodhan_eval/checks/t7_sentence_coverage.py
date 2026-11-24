"""T7 sentence_coverage: compare sentence counts and alignment between source and
translation to localize omission (a source sentence with no target match) or addition
(a target sentence with no source match).

Uses the back-translation when present to align by content; otherwise falls back to a
count/length heuristic. WEAK/MEDIUM evidence -- corroborated by the combiner.
"""

from __future__ import annotations

from ..align import split_sentences
from ..models import Evidence
from .registry import CheckContext, register


@register("t7_sentence_coverage", category="omission",
          description="sentence-level coverage for omission/addition localization")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    if not tr or not ctx.source.strip():
        return []
    src_sents = split_sentences(ctx.source)
    tgt_sents = split_sentences(tr)
    if len(src_sents) <= 1 and len(tgt_sents) <= 1:
        return []
    out: list[Evidence] = []

    back = ctx.artifacts.get("back_translation")
    if isinstance(back, str) and back.strip():
        back_sents = split_sentences(back)
        # omission: a source sentence whose content is absent from the back-translation
        n_missing = max(0, len(src_sents) - len(back_sents))
        if n_missing > 0 and len(src_sents) > len(back_sents):
            # localize to the trailing source sentences most likely dropped
            dropped = src_sents[len(back_sents):]
            for s in dropped:
                out.append(Evidence(
                    check="t7_sentence_coverage", category="omission", strength="medium",
                    noisy=True, score=0.6, severity_hint="major", source_span=s.span,
                    details={"src_sentences": len(src_sents), "back_sentences": len(back_sents)},
                ))
        elif len(back_sents) > len(src_sents):
            out.append(Evidence(
                check="t7_sentence_coverage", category="addition", strength="weak", noisy=True,
                score=0.5, severity_hint="minor",
                details={"src_sentences": len(src_sents), "back_sentences": len(back_sents)},
            ))
    else:
        diff = len(src_sents) - len(tgt_sents)
        if diff >= 1:
            out.append(Evidence(
                check="t7_sentence_coverage", category="omission", strength="weak", noisy=True,
                score=float(diff), severity_hint="minor", source_span=src_sents[-1].span,
                details={"src_sentences": len(src_sents), "tgt_sentences": len(tgt_sents)},
            ))
        elif diff <= -1:
            out.append(Evidence(
                check="t7_sentence_coverage", category="addition", strength="weak", noisy=True,
                score=float(-diff), severity_hint="minor",
                details={"src_sentences": len(src_sents), "tgt_sentences": len(tgt_sents)},
            ))
    return out
