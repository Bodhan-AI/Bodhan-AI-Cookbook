"""Combine check signals and judge verdicts into scored flags.

confidence = noisy-OR over independent checks, then
  judge confirms -> lifted towards 1;  judge rejects -> scaled down (less for hard structural checks);
  judge-only error -> the judge's own confidence.
A flag is reported when confidence >= the per-category threshold; everything below is kept in
`rejected` so calibration can re-tune thresholds without re-running any model.
"""

from __future__ import annotations

from .config import Thresholds
from .judge.judge import Candidate, JudgeOutcome, _overlap, group_candidates
from .models import Category, Flag, JudgeVerdict, Severity, Signal

SEVERITY_ORDER = {Severity.MINOR: 0, Severity.MAJOR: 1, Severity.CRITICAL: 2}
# A judge-only finding has no second witness; its own severity call is the best prior on whether it is real.
JUDGE_ONLY_SEVERITY_FACTOR = {Severity.MINOR: 0.6, Severity.MAJOR: 0.9, Severity.CRITICAL: 1.0}


def noisy_or(signals: list[Signal]) -> float:
    best: dict[str, float] = {}
    for s in signals:
        best[s.check] = max(best.get(s.check, 0.0), s.score)
    miss = 1.0
    for score in best.values():
        miss *= 1.0 - min(max(score, 0.0), 1.0)
    return 1.0 - miss


def _excerpt(text: str | None, span: tuple[int, int] | None, limit: int = 160) -> str | None:
    if not text or span is None or span[1] <= span[0]:
        return None
    piece = text[span[0] : span[1]]
    return piece if len(piece) <= limit else piece[: limit - 3] + "..."


def _max_severity(values: list[Severity]) -> Severity:
    return max(values, key=lambda s: SEVERITY_ORDER[s]) if values else Severity.MAJOR


def _union(spans: list[tuple[int, int] | None]) -> tuple[int, int] | None:
    real = [s for s in spans if s is not None]
    if not real:
        return None
    return (min(s for s, _ in real), max(e for _, e in real))


def _primary_span(signals: list[Signal], attr: str) -> tuple[int, int] | None:
    """Span of the strongest signal that has one; unions blur localization, so avoid them."""
    with_span = [s for s in signals if getattr(s, attr) is not None]
    if not with_span:
        return None
    return getattr(max(with_span, key=lambda s: (s.score, s.hard)), attr)


def fuse(
    *, translation: str, source: str, signals: list[Signal], candidates: list[Candidate] | None, outcome: JudgeOutcome | None, th: Thresholds
) -> tuple[list[Flag], list[Flag]]:
    text_signals = [s for s in signals if s.category.area == "translation"]
    speech_signals = [s for s in signals if s.category.area == "speech"]
    cands = candidates if candidates is not None else group_candidates(text_signals)
    scored: list[Flag] = []
    absorbed: set[int] = set()

    judge_errors = list(outcome.errors) if outcome else []
    for cand in cands:
        sigs = cand.signals
        base = noisy_or(sigs)
        hard = any(s.hard for s in sigs)
        verdict = outcome.verdicts.get(cand.id) if outcome else None
        matched = [k for k, e in enumerate(judge_errors) if _matches(cand, e)]
        same_cat = [k for k in matched if judge_errors[k].category == cand.lead.category]
        other_cat = [k for k in matched if judge_errors[k].category != cand.lead.category]
        category = cand.lead.category
        evidence = [f"[{s.check}] {s.message}" for s in sigs]
        judge_note = None
        severities = [s.severity for s in sigs]
        if same_cat or (verdict and verdict[0] == "confirm"):
            floor = max(base, 0.5)
            confidence = floor + (1 - floor) * (0.5 + th.judge_confirm_bonus)
            absorbed.update(same_cat)
            for k in same_cat:
                severities.append(judge_errors[k].severity)
            judge_note = "confirmed" + (f": {verdict[1]}" if verdict and verdict[1] else "")
            if not same_cat and other_cat and not hard:
                # The judge agrees something is wrong here but names a different category; trust the reader.
                category = judge_errors[other_cat[0]].category
                absorbed.update(other_cat[:1])
        elif verdict and verdict[0] == "reject":
            confidence = base * ((1 + th.judge_reject_factor) / 2 if hard else th.judge_reject_factor)
            judge_note = f"rejected: {verdict[1]}" if verdict[1] else "rejected"
            if hard:
                absorbed.update(other_cat)
        else:
            confidence = base
            if outcome is not None and other_cat and hard:
                absorbed.update(other_cat)
        span = _primary_span(sigs, "span")
        source_span = _primary_span(sigs, "source_span")
        if same_cat:
            j = judge_errors[same_cat[0]]
            span = span or j.span
            source_span = source_span or j.source_span
        scored.append(
            _flag(
                category,
                _max_severity(severities),
                confidence,
                span,
                source_span,
                None,
                translation,
                source,
                evidence,
                sorted({s.check for s in sigs}),
                judge_note,
            )
        )

    for k, err in enumerate(judge_errors):
        if k in absorbed:
            continue
        scored.append(
            _flag(
                err.category,
                err.severity,
                err.confidence * JUDGE_ONLY_SEVERITY_FACTOR[err.severity],
                err.span,
                err.source_span,
                None,
                translation,
                source,
                [f"[llm_judge] {err.explanation}"],
                ["llm_judge"],
                "judge-only finding",
            )
        )

    for cand in group_candidates(speech_signals):
        sigs = cand.signals
        audio_spans = [s.audio_span for s in sigs if s.audio_span]
        audio_span = (min(a for a, _ in audio_spans), max(b for _, b in audio_spans)) if audio_spans else None
        scored.append(
            _flag(
                cand.lead.category,
                _max_severity([s.severity for s in sigs]),
                noisy_or(sigs),
                _primary_span(sigs, "span"),
                None,
                audio_span,
                translation,
                source,
                [f"[{s.check}] {s.message}" for s in sigs],
                sorted({s.check for s in sigs}),
                None,
            )
        )

    scored = _merge_duplicates(scored)
    flags = [f for f in scored if f.confidence >= th.threshold(f.category)]
    rejected = [f for f in scored if f.confidence < th.threshold(f.category)]
    return _order(flags), _order(rejected)


def _matches(cand: Candidate, err: JudgeVerdict) -> bool:
    lead = cand.lead
    spans = [s.span for s in cand.signals if s.span]
    src_spans = [s.source_span for s in cand.signals if s.source_span]
    if err.span and spans and any(_overlap(err.span, s) for s in spans):
        return True
    if err.source_span and src_spans and any(_overlap(err.source_span, s) for s in src_spans):
        return True
    return not spans and not src_spans and err.category == lead.category


def _flag(
    category: Category,
    severity: Severity,
    confidence: float,
    span,
    source_span,
    audio_span,
    translation: str,
    source: str,
    evidence: list[str],
    checks: list[str],
    judge: str | None,
) -> Flag:
    return Flag(
        category=category,
        area=category.area,
        severity=severity,
        confidence=round(min(max(confidence, 0.0), 1.0), 4),
        span=span,
        source_span=source_span,
        audio_span=audio_span,
        excerpt=_excerpt(translation, span),
        source_excerpt=_excerpt(source, source_span),
        evidence=evidence,
        checks=checks,
        judge=judge,
    )


def _merge_duplicates(flags: list[Flag]) -> list[Flag]:
    out: list[Flag] = []
    for f in sorted(flags, key=lambda x: -x.confidence):
        dup = next((o for o in out if o.category == f.category and _same_place(o, f)), None)
        if dup is None:
            out.append(f)
            continue
        dup.evidence = list(dict.fromkeys(dup.evidence + f.evidence))
        dup.checks = sorted(set(dup.checks) | set(f.checks))
    return out


def _same_place(a: Flag, b: Flag) -> bool:
    if a.span and b.span:
        return _overlap(a.span, b.span)
    if a.source_span and b.source_span:
        return _overlap(a.source_span, b.source_span)
    return a.span is None and b.span is None and a.source_span is None and b.source_span is None


def _order(flags: list[Flag]) -> list[Flag]:
    return sorted(flags, key=lambda f: (-SEVERITY_ORDER[f.severity], -f.confidence, f.category.value, f.span or (0, 0)))


def mqm(flags: list[Flag], source: str) -> tuple[float, float]:
    """(penalty points, quality score 0-100 normalized per source word, MQM style)."""
    penalty = float(sum(f.severity.weight for f in flags))
    n_words = max(len(source.split()), 1)
    return penalty, round(max(0.0, 100.0 - penalty / n_words * 100.0), 2)
