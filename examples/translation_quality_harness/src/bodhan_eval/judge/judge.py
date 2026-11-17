"""LLM judge: adjudicates automatic findings and adds errors only a reader can see (mistranslation, subtle omission)."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .. import log
from ..clients.llm import LLMClient
from ..languages import script_of
from ..models import Category, JudgeVerdict, Signal, normalize_category, normalize_severity
from ..text.segment import Piece, tokens
from .rubric import RUBRIC_VERSION, SYSTEM_PROMPT, build_user_prompt

CONFIDENCE = {"high": 0.9, "medium": 0.7, "low": 0.45}


@dataclass
class Candidate:
    id: str
    signals: list[Signal]

    @property
    def lead(self) -> Signal:
        return max(self.signals, key=lambda s: s.score)


@dataclass
class JudgeOutcome:
    verdicts: dict[str, tuple[str, str]] = field(default_factory=dict)
    errors: list[JudgeVerdict] = field(default_factory=list)
    cached: bool = False
    redactions: int = 0

    def signature(self) -> tuple[bool, tuple[str, ...]]:
        cats = sorted({e.category.value for e in self.errors})
        return (bool(cats), tuple(cats))


def redact_indic(text: str) -> tuple[str, int]:
    """Drop any non-Latin letters (and their marks) from judge prose; returns (clean, removed_chars)."""
    out, removed, dropping = [], 0, False
    for ch in text:
        cat = unicodedata.category(ch)
        script = script_of(ch)
        foreign = (cat.startswith("L") and script not in (None, "Latn")) or (cat.startswith("M") and dropping)
        if foreign:
            removed += 1
            if not dropping:
                out.append("[...]")
            dropping = True
            continue
        dropping = False
        out.append(ch)
    return "".join(out), removed


def parse_confidence(value: Any) -> float:
    """Accept 0-100, 0-1 or high/medium/low; default to medium."""
    if isinstance(value, bool):
        return 0.7
    if isinstance(value, int | float):
        v = float(value)
        return round(min(max(v / 100.0 if v > 1.0 else v, 0.0), 1.0), 3)
    text = str(value or "").strip().lower().rstrip("%")
    try:
        return parse_confidence(float(text))
    except ValueError:
        return CONFIDENCE.get(text, 0.7)


def _char_to_tokens(pieces: list[Piece], span: tuple[int, int] | None) -> list[int] | None:
    if span is None or not pieces:
        return None
    s, e = span
    if e <= s:
        idx = next((i for i, p in enumerate(pieces) if p.start >= s), len(pieces) - 1)
        return [idx, idx]
    hit = [i for i, p in enumerate(pieces) if p.start < e and p.end > s]
    return [hit[0], hit[-1]] if hit else None


def _tokens_to_chars(pieces: list[Piece], rng: Any) -> tuple[int, int] | None:
    if not isinstance(rng, list | tuple) or len(rng) != 2 or not pieces:
        return None
    try:
        a, b = int(rng[0]), int(rng[1])
    except (TypeError, ValueError):
        return None
    a, b = min(a, b), max(a, b)
    if b < 0 or a >= len(pieces):
        return None
    a, b = max(a, 0), min(b, len(pieces) - 1)
    return (pieces[a].start, pieces[b].end)


def group_candidates(signals: list[Signal]) -> list[Candidate]:
    groups: list[Candidate] = []
    for sig in sorted(signals, key=lambda s: (s.category.value, s.span or (-1, -1), s.source_span or (-1, -1), s.check)):
        for cand in groups:
            lead = cand.lead
            if lead.category == sig.category and _overlap(lead.span, sig.span) and _overlap(lead.source_span, sig.source_span):
                cand.signals.append(sig)
                break
        else:
            groups.append(Candidate(f"C{len(groups) + 1}", [sig]))
    return groups


def _overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    if a is None or b is None:
        return True
    return a[0] <= b[1] and b[0] <= a[1]


class Judge:
    def __init__(self, llm: LLMClient, votes: int = 1):
        self.llm = llm
        self.votes = max(1, votes)

    @property
    def available(self) -> bool:
        return self.llm.available

    async def judge(
        self,
        *,
        language: str,
        source: str,
        translation: str,
        back_translation: str | None,
        reference: str | None,
        candidates: list[Candidate],
        evidence: dict,
        variant: int = 0,
    ) -> JudgeOutcome:
        if self.votes == 1:
            return await self._once(language, source, translation, back_translation, reference, candidates, evidence, variant)
        outcomes = [
            await self._once(language, source, translation, back_translation, reference, candidates, evidence, variant * self.votes + k)
            for k in range(self.votes)
        ]
        return majority(outcomes)

    async def _once(self, language, source, translation, back_translation, reference, candidates, evidence, variant) -> JudgeOutcome:
        src_pieces, tgt_pieces = tokens(source), tokens(translation)
        payload = []
        for cand in candidates:
            lead = cand.lead
            payload.append(
                {
                    "id": cand.id,
                    "category": lead.category.value,
                    "finding": lead.message,
                    "translation_tokens": _char_to_tokens(tgt_pieces, lead.span),
                    "source_tokens": _char_to_tokens(src_pieces, lead.source_span),
                }
            )
        user = build_user_prompt(
            language=language,
            source_tokens=[p.text for p in src_pieces],
            translation_tokens=[p.text for p in tgt_pieces],
            back_translation=back_translation,
            reference=reference,
            candidates=payload,
            evidence=evidence,
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        raw, cached = await self.llm.complete_json(messages, namespace=f"judge:{RUBRIC_VERSION}", variant=variant)
        return parse_outcome(raw, src_pieces, tgt_pieces, {c.id for c in candidates}, cached)


def parse_outcome(raw: Any, src_pieces: list[Piece], tgt_pieces: list[Piece], candidate_ids: set[str], cached: bool) -> JudgeOutcome:
    outcome = JudgeOutcome(cached=cached)
    if not isinstance(raw, dict):
        return outcome
    for entry in raw.get("candidates") or []:
        if not isinstance(entry, dict) or entry.get("id") not in candidate_ids:
            continue
        verdict = str(entry.get("verdict", "")).lower()
        reason, removed = redact_indic(str(entry.get("reason", "")))
        outcome.redactions += removed
        outcome.verdicts[entry["id"]] = ("confirm" if verdict.startswith("confirm") else "reject", reason[:400])
    for entry in raw.get("errors") or []:
        if not isinstance(entry, dict):
            continue
        category = normalize_category(str(entry.get("category", "")))
        if category is None or category.area != "translation":
            continue
        span = _tokens_to_chars(tgt_pieces, entry.get("translation_tokens"))
        source_span = _tokens_to_chars(src_pieces, entry.get("source_tokens"))
        if span is None and source_span is None:
            continue
        explanation, removed = redact_indic(str(entry.get("explanation", "")))
        outcome.redactions += removed
        confidence = parse_confidence(entry.get("confidence"))
        says = [str(entry.get(k) or "").strip() for k in ("source_says", "translation_says")]
        if all(says):
            explanation = f"{explanation} (source: {says[0]}; translation conveys: {says[1]})"
            explanation, removed2 = redact_indic(explanation)
            outcome.redactions += removed2
        outcome.errors.append(
            JudgeVerdict(
                category=category,
                severity=normalize_severity(entry.get("severity")),
                confidence=confidence,
                span=span,
                source_span=source_span,
                explanation=explanation[:500],
            )
        )
    if outcome.redactions:
        log.warn("judge_redacted_target_language_text", count=outcome.redactions)
    return outcome


def majority(outcomes: list[JudgeOutcome]) -> JudgeOutcome:
    need = math.ceil(len(outcomes) / 2)
    merged = JudgeOutcome(cached=all(o.cached for o in outcomes), redactions=sum(o.redactions for o in outcomes))
    ids = {cid for o in outcomes for cid in o.verdicts}
    for cid in sorted(ids):
        votes = [o.verdicts[cid] for o in outcomes if cid in o.verdicts]
        confirms = [v for v in votes if v[0] == "confirm"]
        merged.verdicts[cid] = confirms[0] if len(confirms) >= need else next((v for v in votes if v[0] == "reject"), ("reject", ""))
    clusters: list[list[JudgeVerdict]] = []
    for o in outcomes:
        for err in o.errors:
            for cluster in clusters:
                head = cluster[0]
                if head.category == err.category and _overlap(head.span, err.span) and _overlap(head.source_span, err.source_span):
                    cluster.append(err)
                    break
            else:
                clusters.append([err])
    for cluster in clusters:
        if len(cluster) >= need:
            best = max(cluster, key=lambda e: e.confidence)
            merged.errors.append(best.model_copy(update={"confidence": round(best.confidence * len(cluster) / len(outcomes), 3)}))
    return merged


def is_target_category(category: Category) -> bool:
    return category.area == "translation"
