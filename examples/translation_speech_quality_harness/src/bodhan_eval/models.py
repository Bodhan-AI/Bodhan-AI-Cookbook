"""Plain data classes passed between pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

Span = tuple[int, int]


@dataclass
class Label:
    """A human (or injector) error label, normalised to our taxonomy."""

    category: str | None  # canonical category, None if the type is unknown
    raw_type: str
    severity: str
    span: Span | None = None  # character offsets; see ``span_on``
    span_on: str = "translation"  # "translation" | "source"
    annotator: str | None = None


@dataclass
class Item:
    id: str
    lang: str
    source: str
    reference: str | None = None
    translation: str | None = None
    audio: str | None = None  # resolved absolute path, or None
    labels: list[Label] | None = None  # None = unlabelled; [] = labelled clean
    line_no: int = 0
    generated: list[str] = field(default_factory=list)  # which outputs we generated ("translation", "audio")
    calibration_only: bool = False  # came from --calibration only; used to calibrate, kept out of the main summary

    @property
    def key(self) -> str:
        return f"{self.id}::{self.lang}"


@dataclass
class Signal:
    """One piece of evidence from one check. Several signals may merge into one flag."""

    check: str
    category: str
    score: float  # 0..1, roughly "probability this is a real error" from this check alone
    evidence: str
    severity: str = "major"
    span: Span | None = None  # in the translation
    source_span: Span | None = None  # in the English source
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = round(self.score, 4)
        return d


@dataclass
class Flag:
    category: str
    severity: str
    confidence: float
    span: Span | None
    source_span: Span | None
    text: str | None  # the flagged translation text, copied by code (never written by the LLM)
    source_text: str | None
    evidence: list[dict]
    checks: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = round(self.confidence, 4)
        return d


@dataclass
class ItemResult:
    item: Item
    status: str = "ok"  # ok | skipped | error
    reason: str | None = None
    checks: dict[str, dict] = field(default_factory=dict)  # name -> {status, notes, metrics}
    metrics: dict[str, Any] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    uncertain: list[Flag] = field(default_factory=list)
    dismissed: list[dict] = field(default_factory=list)  # candidates explained away as checker noise
    state: dict[str, Any] = field(default_factory=dict)  # scratch space shared by checks; not reported
