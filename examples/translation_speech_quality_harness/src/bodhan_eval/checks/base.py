"""Shared result types for all checks.

A check never decides the final verdict. It emits *candidate* flags with a confidence and
evidence; `combine.py` merges candidates across checks and applies calibrated thresholds.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from bodhan_eval.normalize import split_sentences


@dataclass
class Flag:
    category: str  # canonical category (categories.py)
    severity: str  # minor | major | critical
    check: str  # name of the check that produced it
    confidence: float  # 0..1, the check's own belief this is a real system error
    span: tuple[int, int] | None  # char span in the evaluated text (translation / spoken text)
    evidence: str  # human-readable, concrete: what was expected vs found
    source_span: tuple[int, int] | None = None  # char span in the English source, if known
    time_span: tuple[float, float] | None = None  # seconds, for audio flags
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("span", "source_span", "time_span"):
            if d[k] is not None:
                d[k] = list(d[k])
        return d


@dataclass
class CheckResult:
    check: str
    ran: bool = True
    flags: list[Flag] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)  # metrics used for routing/combining
    skipped_reason: str | None = None

    @classmethod
    def skipped(cls, check: str, reason: str) -> CheckResult:
        return cls(check=check, ran=False, skipped_reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "ran": self.ran,
            "skipped_reason": self.skipped_reason,
            "signals": self.signals,
            "flags": [f.to_dict() for f in self.flags],
        }


def map_source_span(
    source: str, target: str, src_span: tuple[int, int]
) -> tuple[tuple[int, int], bool]:
    """Best-effort mapping of a source span to the corresponding target region.

    If both texts have the same number of sentences, returns the matching target sentence
    (exact=True). Otherwise falls back to the proportional character position (exact=False).
    """
    s_sents, t_sents = split_sentences(source), split_sentences(target)
    if s_sents and len(s_sents) == len(t_sents):
        for i, (s, e) in enumerate(s_sents):
            if s <= src_span[0] < e or (i == len(s_sents) - 1):
                return t_sents[i], True
    n_s, n_t = max(1, len(source)), len(target)
    a = int(src_span[0] / n_s * n_t)
    b = max(a + 1, int(src_span[1] / n_s * n_t))
    return (min(a, n_t), min(b, n_t)), False


def line_span(text: str, pos: int) -> tuple[int, int]:
    """Span of the line containing `pos`, without the newline."""
    s = text.rfind("\n", 0, pos) + 1
    e = text.find("\n", pos)
    return (s, len(text) if e == -1 else e)
