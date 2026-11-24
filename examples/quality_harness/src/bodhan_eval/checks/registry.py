"""Check registry and the context object passed to every deterministic check.

A deterministic check is a pure-ish function `(ctx) -> list[Evidence]`: it reads the item
and shared artifacts and emits Evidence. It never decides whether to flag (the combiner
does) and never calls a paid model (round-trip/judge/ASR live in their own modules).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import LanguageConfig, Thresholds
from ..models import Item

CheckFn = Callable[["CheckContext"], list]


@dataclass
class CheckSpec:
    name: str
    fn: CheckFn
    category: str
    description: str


_REGISTRY: dict[str, CheckSpec] = {}


def register(name: str, category: str, description: str = "") -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        if name in _REGISTRY:
            raise ValueError(f"duplicate check name: {name}")
        _REGISTRY[name] = CheckSpec(name=name, fn=fn, category=category, description=description)
        return fn

    return deco


def all_checks() -> list[CheckSpec]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


@dataclass
class CheckContext:
    """Everything a deterministic check may read. `corpus` enables cross-item checks
    (e.g. term consistency). `artifacts` holds shared, possibly model-derived data such as
    a back-translation, keyed by name, so checks don't recompute or re-pay."""

    item: Item
    lang: str
    thresholds: Thresholds
    languages: LanguageConfig
    artifacts: dict = field(default_factory=dict)
    corpus: Corpus | None = None

    @property
    def source(self) -> str:
        return self.item.source or ""

    @property
    def translation(self) -> str | None:
        return self.item.translation

    @property
    def reference(self) -> str | None:
        return self.item.reference


@dataclass
class Corpus:
    """Cross-item view for consistency checks: term -> {lang -> {rendering -> [item_ids]}}."""

    items: list[Item]
    term_renderings: dict[str, dict[str, dict[str, list[str]]]] = field(default_factory=dict)
