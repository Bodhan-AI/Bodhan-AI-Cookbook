"""Central data model shared by every stage of the harness.

Keeping the taxonomy and the Evidence/Flag/ItemResult shapes in one place means a new
check only has to emit Evidence objects; the combiner, reports and calibration all speak
the same language.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# ---- Taxonomy (use these exact ids everywhere) ----------------------------------------

TRANSLATION_CATEGORIES = (
    "omission",
    "addition",
    "mistranslation",
    "number_unit",
    "term_entity",
    "format",
    "wrong_language_script",
)
SPEECH_CATEGORIES = (
    "speech_omission",
    "speech_repetition",
    "speech_garbled",
)
ALL_CATEGORIES = TRANSLATION_CATEGORIES + SPEECH_CATEGORIES

Category = str  # one of ALL_CATEGORIES (kept as str for forward-compat / unknown labels)
Severity = Literal["minor", "major", "critical"]
Strength = Literal["strong", "medium", "weak"]
Status = Literal["clean", "flagged", "uncertain", "skipped"]

SEVERITY_WEIGHT = {"minor": 1, "major": 5, "critical": 10}

# A span is a half-open [start, end) character range in ORIGINAL (un-normalized) coordinates.
Span = tuple[int, int]


def is_speech_category(cat: str) -> bool:
    return cat in SPEECH_CATEGORIES


# ---- Input item -----------------------------------------------------------------------


@dataclass
class Label:
    """A human/injector-provided ground-truth label for calibration."""

    type: str
    severity: Severity = "major"
    span: Span | None = None
    span_space: Literal["source", "target"] = "source"
    annotator: str | None = None  # "candidate" | "injector" | provider labels have none

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Label:
        span = d.get("span")
        if span is not None:
            span = (int(span[0]), int(span[1]))
        return Label(
            type=d["type"],
            severity=d.get("severity", "major"),
            span=span,
            span_space=d.get("span_space", "source"),
            annotator=d.get("annotator") or d.get("label_source"),
        )


@dataclass
class Item:
    """One dataset line. Only id, source, lang are required."""

    id: str
    source: str
    lang: str
    reference: str | None = None
    translation: str | None = None
    audio: str | None = None
    labels: list[Label] = field(default_factory=list)
    label_source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Item:
        labels = [Label.from_dict(x) for x in d.get("labels", []) or []]
        return Item(
            id=str(d["id"]),
            source=d["source"],
            lang=d["lang"],
            reference=d.get("reference"),
            translation=d.get("translation"),
            audio=d.get("audio"),
            labels=labels,
            label_source=d.get("label_source"),
            raw=d,
        )


# ---- Evidence / Flag / result ---------------------------------------------------------


@dataclass
class Evidence:
    """One signal from one check. Checks NEVER flag directly; they emit Evidence and the
    combiner decides. `noisy=True` means this checker can be wrong on its own (needs
    corroboration before it can raise a flag)."""

    check: str
    category: Category
    strength: Strength
    noisy: bool
    score: float = 0.0
    severity_hint: Severity = "major"
    source_span: Span | None = None
    target_span: Span | None = None
    details: dict[str, Any] = field(default_factory=dict)
    artifact_refs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Flag:
    category: Category
    severity: Severity
    confidence: float
    source_span: Span | None = None
    target_span: Span | None = None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class ItemResult:
    id: str
    lang: str
    status: Status = "clean"
    checks_run: list[str] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    skip_reason: str | None = None
    cost_inr: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Deterministic, stable-key dict for items.jsonl (no timestamps)."""
        return {
            "id": self.id,
            "lang": self.lang,
            "status": self.status,
            "skip_reason": self.skip_reason,
            "checks_run": sorted(self.checks_run),
            "flags": [_flag_json(f) for f in self.flags],
            "evidence": [_evidence_json(e) for e in self.evidence],
            "cost_inr": round(self.cost_inr, 6),
            "details": self.details,
        }


def _span_json(s: Span | None) -> list[int] | None:
    return [s[0], s[1]] if s is not None else None


def _evidence_json(e: Evidence) -> dict[str, Any]:
    return {
        "check": e.check,
        "category": e.category,
        "strength": e.strength,
        "noisy": e.noisy,
        "score": round(float(e.score), 6),
        "severity_hint": e.severity_hint,
        "source_span": _span_json(e.source_span),
        "target_span": _span_json(e.target_span),
        "details": e.details,
        "artifact_refs": e.artifact_refs,
    }


def _flag_json(f: Flag) -> dict[str, Any]:
    return {
        "category": f.category,
        "severity": f.severity,
        "confidence": round(float(f.confidence), 4),
        "source_span": _span_json(f.source_span),
        "target_span": _span_json(f.target_span),
        "evidence": [_evidence_json(e) for e in f.evidence],
    }


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    return asdict(obj)
