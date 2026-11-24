"""The check interface. A check reads an item (plus shared artifacts) and emits Signals.

A Signal is a *suspicion with evidence*, not a verdict: `fuse.py` decides which signals become
flags. To add a check: write a class with `name`, `area`, `tier`, `applies()` and `run()`,
decorate it with `@register`, and give it a weight in `thresholds.json`. Nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..dataset import Item
from ..langs import Lang
from ..taxonomy import area_of


@dataclass
class Signal:
    check: str
    category: str
    severity: str
    confidence: float  # the check's own belief that this is a real system error, 0..1
    message: str  # English, human readable
    span: tuple[int, int] | None = None  # offsets into the translation (also for speech errors)
    source_span: tuple[int, int] | None = None  # offsets into the English source
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def area(self) -> str:
        return area_of(self.category)

    def to_dict(self) -> dict:
        return {
            "check": self.check, "category": self.category, "severity": self.severity, "confidence": round(self.confidence, 3),
            "message": self.message, "span": list(self.span) if self.span else None,
            "source_span": list(self.source_span) if self.source_span else None, "evidence": self.evidence,
        }


@dataclass
class Context:
    item: Item
    lang: Lang
    translation: str
    stats: dict[str, Any]  # dataset-level statistics (e.g. length-ratio baselines)
    artifacts: dict[str, Any] = field(default_factory=dict)  # back-translation, judge output, ASR, ...
    checks_run: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Check:
    name: str = "check"
    area: str = "translation"
    tier: int = 0  # 0 = free/deterministic, 1 = cheap model call, 2 = LLM, 3 = costly (speech synthesis)

    def applies(self, ctx: Context) -> bool:
        return bool(ctx.translation)

    async def run(self, ctx: Context) -> list[Signal]:  # pragma: no cover - interface
        raise NotImplementedError


REGISTRY: dict[str, Check] = {}


def register(cls):
    REGISTRY[cls.name] = cls()
    return cls
