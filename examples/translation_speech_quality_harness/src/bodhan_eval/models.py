from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


Span = tuple[int, int]


@dataclass(frozen=True)
class Label:
    type: str
    severity: str
    span: Span | list[float] | None
    annotator: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Item:
    id: str
    lang: str
    source: str
    translation: str | None = None
    reference: str | None = None
    audio: Path | None = None
    labels: list[Label] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Flag:
    category: str
    severity: str
    modality: str
    span: Span | None = None
    source_span: Span | None = None
    audio_span_s: tuple[float, float] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    detectors: list[str] = field(default_factory=list)
    confidence: float = 0.0
    suppressed_reason: str | None = None
