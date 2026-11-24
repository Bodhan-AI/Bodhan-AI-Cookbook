"""Library entry points: the same checks as the command, callable from other code (e.g. a QA gate in a dubbing
or narrated-lesson pipeline). Independent of the CLI: nothing here parses arguments or writes report files.

    from bodhan_eval.api import Harness
    h = Harness(max_cost_inr=5)
    flags = h.check_translation("Water boils at 100 °C.", "पानी 100 °C पर उबलता है।", "hi")
    flags = h.check_speech("पानी 100 °C पर उबलता है।", "clip.wav", "hi", source="Water boils at 100 °C.")
    result = h.evaluate(items)          # list[models.Item] -> pipeline.RunResult (flags per item, calibration)

Keys come from the environment, as for the command. Every call builds its own run context (cache, budget,
clients), so one ``Harness`` can be used from several threads; the cache is shared on disk, so repeated
questions are free. ``max_cost_inr`` caps each call. The per-language statistics (length ratio, speaking
rate) come from the items of one call, so a single item is judged against the language priors in langs.json.
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .dataset import LoadReport
from .models import Flag, Item
from .pipeline import RunOptions, RunResult, _context, execute

SPEECH_CATEGORIES = ("speech_omission", "speech_repetition", "speech_garbled")


class Harness:
    def __init__(
        self,
        cache_dir: str | Path = ".cache/bodhan_eval",
        max_cost_inr: float | None = None,
        judge: bool = True,
        speech: bool = True,
        llm_extra_body: dict[str, Any] | None = None,
        concurrency: int = 6,
    ):
        self._work = Path(tempfile.mkdtemp(prefix="bodhan_eval_api_"))  # the per-call event log goes here
        self.options = RunOptions(
            dataset=Path("api"),
            out=self._work,
            cache_dir=Path(cache_dir),
            max_cost_inr=max_cost_inr,
            judge=judge,
            speech=speech,
            llm_extra_body=llm_extra_body,
            concurrency=concurrency,
        )

    def evaluate(self, items: list[Item]) -> RunResult:
        opts = replace(self.options)
        ctx = _context(opts)
        try:
            return execute(items, LoadReport(), ctx, opts)
        finally:
            ctx.events.close()
            ctx.cache.close()

    def check_translation(self, source: str, translation: str, lang: str, reference: str | None = None) -> list[Flag]:
        item = Item(id="api", lang=lang, source=source, translation=translation, reference=reference)
        return self.evaluate([item]).results[0].flags

    def check_speech(
        self, text: str, audio: str | Path | bytes, lang: str, source: str | None = None
    ) -> list[Flag]:
        """Speech flags for ``audio`` (a WAV path or WAV bytes) meant to say ``text``. With ``source`` (the English)
        the translation checks run too, but only speech flags are returned."""
        if isinstance(audio, bytes):
            path = self._work / f"clip_{abs(hash(audio))}.wav"
            path.write_bytes(audio)
            audio = path
        item = Item(id="api", lang=lang, source=source or text, translation=text, audio=str(Path(audio).resolve()))
        return [f for f in self.evaluate([item]).results[0].flags if f.category in SPEECH_CATEGORIES]
