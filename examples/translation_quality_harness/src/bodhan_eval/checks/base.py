"""Per-item working state that checks read from and append signals to."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..audio.analysis import AudioStats
from ..audio.io import Audio
from ..languages import Language
from ..models import CheckResult, Item, Signal
from ..text.speakable import Speakable


@dataclass
class ItemContext:
    item: Item
    lang: Language
    uid: str = ""
    translation: str | None = None
    back_translation: str | None = None
    audio: Audio | None = None
    audio_path: str | None = None
    audio_stats: AudioStats | None = None
    speakable: Speakable | None = None
    transcript: str | None = None
    transcript_chunks: list[dict] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed: str | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)
    candidates: list | None = None

    @property
    def source(self) -> str:
        return self.item.source

    def add(self, signal: Signal) -> None:
        self.signals.append(signal)

    @contextmanager
    def check(self, name: str, stage: str) -> Iterator[CheckResult]:
        result = CheckResult(name=name, stage=stage)
        before = len(self.signals)
        started = time.perf_counter()
        try:
            yield result
        except Exception as exc:  # noqa: BLE001 - one broken check must not sink the item
            result.status = "error"
            result.reason = f"{type(exc).__name__}: {exc}"
        finally:
            self.timings_ms[name] = int((time.perf_counter() - started) * 1000)
            result.signals = len(self.signals) - before
            self.checks.append(result)


def skip(ctx: ItemContext, name: str, stage: str, reason: str) -> None:
    ctx.checks.append(CheckResult(name=name, stage=stage, status="skipped", reason=reason))


CheckFn = Callable[..., None]
