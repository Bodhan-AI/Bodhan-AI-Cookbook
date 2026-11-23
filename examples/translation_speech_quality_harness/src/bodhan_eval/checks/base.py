"""Check plug-in interface.

A check reads an ``ItemResult`` (the item plus what earlier checks stored in
``r.state``), appends ``Signal``s and metrics, and returns. It never decides on
its own whether something becomes a flag; ``fusion.py`` does that from all the
signals, using calibrated thresholds.

To add a check: subclass ``Check``, set ``name``/``area``/``tier``, implement
``applies`` and ``run``, and add the class to ``TRANSLATION_CHECKS`` or
``SPEECH_CHECKS`` in ``checks/__init__.py`` (position = run order).

Tiers make the cost order explicit:
  0 = local and deterministic (free, milliseconds)
  1 = Bodhan round trip (cheap: back-translation, transliteration, transcription)
  2 = LLM judge
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..models import ItemResult, Signal

if TYPE_CHECKING:  # pragma: no cover
    from ..pipeline import RunContext


class SkipCheck(Exception):
    """Raise from ``run`` to record a skip with a reason (e.g. nothing to compare)."""


@dataclass
class DatasetStats:
    """Per-language statistics estimated from the dataset itself (so nothing is hand-tuned per language)."""

    length_ratio: dict[str, tuple[float, float, int]] = field(
        default_factory=dict
    )  # lang -> (median log ratio, robust sd, n)
    chars_per_second: dict[str, tuple[float, int]] = field(default_factory=dict)  # lang -> (median rate, n)
    extra: dict[str, Any] = field(default_factory=dict)


class Check:
    name: str = "check"
    area: str = "translation"  # "translation" | "speech"
    tier: int = 0
    description: str = ""

    def applies(self, r: ItemResult, ctx: RunContext) -> bool:
        return True

    def run(self, r: ItemResult, ctx: RunContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def signal(self, r: ItemResult, category: str, score: float, evidence: str, **kw: Any) -> Signal:
        s = Signal(check=self.name, category=category, score=max(0.0, min(1.0, score)), evidence=evidence, **kw)
        r.signals.append(s)
        return s

    def param(self, ctx: RunContext, key: str) -> Any:
        return ctx.params[key]

    def translation(self, r: ItemResult) -> str:
        """The item's translation as ``str``. ``applies`` already requires one; this makes it explicit (and typed)."""
        if r.item.translation is None:
            raise SkipCheck("no translation to check")
        return r.item.translation
