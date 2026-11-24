"""Flag and check primitives.

A flag is never raised without evidence and a span: "score 3.2" helps nobody,
so every check must say what is wrong, where, and how it knows.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable

# Categories the assignment seeds, plus the speech ones.
CATEGORIES = (
    "omission", "addition", "mistranslation", "number", "term", "format",
    "language", "speech_omission", "speech_repetition", "speech_garbled",
)

SEVERITIES = ("minor", "major", "critical")

# MQM-style weights, used only for the optional aggregate score.
SEVERITY_WEIGHT = {"minor": 1, "major": 5, "critical": 10}


@dataclass
class Flag:
    category: str
    severity: str
    span: tuple[int, int] | None
    evidence: str
    check: str
    confidence: float = 1.0
    item_id: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["span"] = list(self.span) if self.span else None
        return d


@dataclass
class ItemContext:
    """Everything a check may look at for one dataset item."""

    id: str
    source: str
    lang: str
    translation: str | None = None
    reference: str | None = None
    audio: str | None = None
    labels: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# A check receives the item and returns flags. Cheap checks take no model.
Check = Callable[[ItemContext], list[Flag]]


class Registry:
    """Ordered registry so cheap checks always run before costly ones."""

    def __init__(self) -> None:
        self._checks: list[tuple[str, int, Check]] = []

    def add(self, name: str, tier: int, fn: Check) -> None:
        self._checks.append((name, tier, fn))

    def ordered(self) -> list[tuple[str, int, Check]]:
        return sorted(self._checks, key=lambda c: c[1])

    def __iter__(self) -> Iterable[tuple[str, int, Check]]:
        return iter(self.ordered())


def dedupe_flags(flags: list[Flag]) -> list[Flag]:
    """Merge flags that describe the same error.

    Several checks can legitimately catch one defect (a wrong-script output also
    looks like a Latin-ratio anomaly). Reporting both double-counts the error in
    precision and confuses the reader, so flags in the same category whose spans
    overlap are merged: highest severity wins, evidence is combined.
    """
    if len(flags) <= 1:
        return flags

    def overlaps(a, b) -> bool:
        if a is None or b is None:
            return a is None and b is None
        return min(a[1], b[1]) > max(a[0], b[0])

    merged: list[Flag] = []
    for f in sorted(flags, key=lambda x: (-SEVERITY_WEIGHT.get(x.severity, 1), -x.confidence)):
        for m in merged:
            if m.category == f.category and overlaps(m.span, f.span):
                if SEVERITY_WEIGHT.get(f.severity, 1) > SEVERITY_WEIGHT.get(m.severity, 1):
                    m.severity = f.severity
                m.confidence = max(m.confidence, f.confidence)
                if f.check not in m.evidence:
                    m.evidence = f"{m.evidence}; also {f.check}: {f.evidence}"[:400]
                m.check = m.check if m.check == f.check else f"{m.check}+{f.check}"
                break
        else:
            merged.append(f)
    return merged
