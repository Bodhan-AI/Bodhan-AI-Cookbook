"""Evidence combiner: turn a bag of Evidence into flags, or mark the item uncertain.

The central rule that separates SYSTEM errors from CHECKER errors:
- Strong deterministic evidence (wrong script, number mismatch, broken markup) can flag alone.
- Noisy evidence (back-translation, ASR, length, chrF, the judge) needs corroboration: a
  second independent noisy check in the same category, or the judge with a VERBATIM-verified
  quote. The judge alone may raise semantic categories (omission/addition/mistranslation/
  term_entity) that deterministic checks cannot see, scaled by its per-language reliability.
- If only a lone noisy checker fires, we do NOT flag: the item is 'uncertain' and goes to the
  review queue, and the disagreement is counted.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Thresholds
from .models import Evidence, Flag, ItemResult, Span

_SEV_RANK = {"minor": 0, "major": 1, "critical": 2}
_SEMANTIC_JUDGE_ONLY = {"omission", "addition", "mistranslation", "term_entity"}


@dataclass
class CombineOutcome:
    flags: list[Flag]
    uncertain_categories: list[str]
    checker_disagreement: bool


def _max_severity(evs: list[Evidence]) -> str:
    return max((e.severity_hint for e in evs), key=lambda s: _SEV_RANK.get(s, 1), default="major")


def _best_span(evs: list[Evidence]) -> tuple[Span | None, Span | None]:
    src = next((e.source_span for e in evs if e.source_span), None)
    tgt = next((e.target_span for e in evs if e.target_span), None)
    return src, tgt


def combine(evidence: list[Evidence], thresholds: Thresholds, lang: str,
            judge_verdict: str | None = None) -> CombineOutcome:
    by_cat: dict[str, list[Evidence]] = {}
    for e in evidence:
        by_cat.setdefault(e.category, []).append(e)

    judge_cut = thresholds.get("judge_confidence_cut", lang)
    judge_mult = thresholds.judge_multiplier(lang)

    flags: list[Flag] = []
    uncertain: list[str] = []
    any_noisy_error_signal = False

    for cat, evs in sorted(by_cat.items()):
        strong_det = [e for e in evs if not e.noisy and e.strength == "strong"]
        medium_det = [e for e in evs if not e.noisy and e.strength == "medium"]
        judge_evs = [e for e in evs if e.check == "judge"]
        judge_located = [e for e in judge_evs if e.details.get("located")
                         and e.score * judge_mult >= judge_cut]
        indep_noisy = {e.check for e in evs if e.noisy and e.check != "judge"}

        if evs:
            any_noisy_error_signal = any_noisy_error_signal or bool(indep_noisy) or bool(judge_located)

        flag: Flag | None = None
        if strong_det:
            flag = _make_flag(cat, strong_det + medium_det, confidence=0.95)
        elif medium_det:
            flag = _make_flag(cat, medium_det, confidence=0.78)
        elif len(indep_noisy) >= 2:
            flag = _make_flag(cat, evs, confidence=0.62)
        elif len(indep_noisy) >= 1 and judge_located:
            flag = _make_flag(cat, evs, confidence=0.72)
        elif judge_located and cat in _SEMANTIC_JUDGE_ONLY:
            conf = min(0.85, max(e.score for e in judge_located) * judge_mult)
            flag = _make_flag(cat, judge_located, confidence=conf)
        elif indep_noisy or judge_evs:
            uncertain.append(cat)

        if flag is not None:
            flags.append(flag)

    # checker disagreement: judge flagged an error nobody corroborated, or checks raised noisy
    # signals the judge did not confirm.
    disagreement = False
    if judge_verdict is not None:
        judged_error = judge_verdict == "error"
        flagged = bool(flags)
        disagreement = (judged_error != flagged) and (any_noisy_error_signal or judged_error)

    return CombineOutcome(flags=flags, uncertain_categories=sorted(set(uncertain)),
                          checker_disagreement=disagreement)


def _make_flag(category: str, evs: list[Evidence], confidence: float) -> Flag:
    src, tgt = _best_span(evs)
    return Flag(category=category, severity=_max_severity(evs), confidence=confidence,
                source_span=src, target_span=tgt, evidence=list(evs))


def finalize_item(result: ItemResult, outcome: CombineOutcome) -> ItemResult:
    result.flags = outcome.flags
    if outcome.flags:
        result.status = "flagged"
    elif outcome.uncertain_categories:
        result.status = "uncertain"
    else:
        result.status = "clean"
    result.details["uncertain_categories"] = outcome.uncertain_categories
    result.details["checker_disagreement"] = outcome.checker_disagreement
    return result
