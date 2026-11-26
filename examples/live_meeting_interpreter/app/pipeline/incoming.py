"""They speak -> you read: meeting audio -> VAD -> Indic-Transcribe -> Indic-Translate -> overlay.

Partial captions: the transcript is shown as soon as it arrives, and the same caption is
updated in place when its translation follows.

Merging: a pause can split one sentence in two ("अभी के लिए मैं / कॉल कट करता हूँ" was
translated as "for now i / cut off call"). If an utterance starts within
SUBTITLE_MERGE_PAUSE_S of a short previous one (a fragment, MERGE_AFTER_MAX_S), the joined
text is translated and replaces the previous caption. ASR output has no punctuation, so the
pause and the fragment's length are the only signals.

Live captions (ASR_BACKEND=local only): while someone talks, the sentence so far is
re-transcribed and translated every LIVE_CAPTION_INTERVAL_S and shown as a provisional
caption; the final caption replaces it when the sentence ends. Live work never delays final
captions: it only runs when the GPU is idle, and it is dropped if its sentence has ended or an
earlier caption is still on its way.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.asr import Busy, Transcriber
from app.audio.vad import Segment, Segmenter
from app.bodhan.client import BodhanClient, BodhanError
from app.config import Settings
from app.pipeline.common import UI, Caption, InOrder
from app.pipeline.text import is_meaningful, tidy

log = logging.getLogger(__name__)
IDLE_POLL_S = 0.25
SILENCE_BLOCK = np.zeros(int(16000 * IDLE_POLL_S), dtype=np.float32)
MERGE_MAX_CHARS = 140  # a joined caption longer than this starts afresh (keeps the bar to ~2 lines)
# Only a short previous utterance is continued: about 2.5 s of speech plus the segmenter's 0.3 s
# pre-roll and 0.2 s tail. Split sentences leave a short fragment ("एक साल के लिए", 1.7 s); longer
# ones are usually whole sentences, and continuing them made captions repeat in fast dialogue.
MERGE_AFTER_MAX_S = 3.0
LIVE_FIRST_S = 2.5     # shorter sentences end soon anyway: no live caption for them
# Live captions only translate while at least half the translate key's per-minute limit (and
# never fewer than LIVE_TRANSLATE_RESERVE requests) stays free for final captions. The key
# advertises 120/min but refused the 17th request within 60 s (2026-11-26), so set
# BODHAN_TRANSLATE_RPM=15 for this to mean anything. Live captions matter on long sentences,
# which come with few final captions; in fast dialogue the finals need the whole budget.
LIVE_TRANSLATE_RESERVE = 5
LIVE_TRANSLATE_TIMEOUT_S = 3.0


def pause_between(earlier: Segment, later: Segment, end_silence_s: float) -> float:
    """Seconds of silence between two utterances.

    A segment's ended_at is the VAD cut, which comes end_silence_s after the speech stopped.
    """
    return later.started_at - (earlier.ended_at - end_silence_s)


@dataclass
class _Job:
    segment: Segment
    # This caption's full original text (including what it merged), or None if it has none.
    # The next utterance awaits it when it continues this one.
    transcript: asyncio.Future[Optional[str]]
    joined: bool = False  # it continued an earlier caption (set before `transcript` resolves)


@dataclass
class _Translation:
    text: str
    mt_s: float
    ready_at: float


@dataclass
class _Partial:
    segment: Segment
    caption: Caption                                   # transcript, translation pending
    asr_s: float
    shown_at: float                                    # when the transcript was ready
    translation: Optional[asyncio.Task[_Translation]]  # None when no translation is needed


class IncomingSubtitles:
    def __init__(self, settings: Settings, client: BodhanClient, asr: Transcriber, ui: UI,
                 segmenter: Segmenter) -> None:
        self._s = settings
        self._client = client
        self._asr = asr
        self._ui = ui
        self._seg = segmenter
        self._live_on = settings.asr_backend == "local" and settings.live_caption_interval_s > 0
        self._emitted = 0                          # seq of the last utterance that ended
        self._live_task: Optional[asyncio.Task[None]] = None
        self._live_started = float("-inf")

    async def run(self, audio: asyncio.Queue, stop: asyncio.Event) -> None:
        runner = InOrder(self._show, self._on_error)
        consumer = asyncio.create_task(runner.run())
        loop = asyncio.get_running_loop()
        previous: Optional[_Job] = None
        try:
            while not stop.is_set():
                try:
                    block = await asyncio.wait_for(audio.get(), IDLE_POLL_S)
                except asyncio.TimeoutError:
                    # WASAPI loopback goes silent (no callbacks) when nothing plays:
                    # feed synthetic silence so an in-progress utterance can end.
                    if not self._seg.in_speech:
                        continue
                    block = SILENCE_BLOCK
                for segment in self._seg.push(block):
                    self._emitted = segment.seq
                    job = _Job(segment, loop.create_future())
                    runner.submit(self._process(job, previous if self._continues(previous, segment) else None))
                    previous = job
                if self._live_on:
                    self._maybe_live(previous, runner)
        finally:
            consumer.cancel()
            if self._live_task:
                self._live_task.cancel()

    def _continues(self, previous: Optional[_Job], segment: Segment) -> bool:
        limit = self._s.subtitle_merge_pause_s
        if previous is None or limit <= 0 or previous.segment.duration_s > MERGE_AFTER_MAX_S:
            return False
        return pause_between(previous.segment, segment, self._s.vad.end_silence_ms / 1000) <= limit

    async def _join(self, continues: Optional[_Job], text: str) -> tuple[str, Optional[int]]:
        """(original text to show and translate, seq it replaces) for an utterance's transcript."""
        if continues is not None:
            earlier = await continues.transcript  # normally done long ago
            # At most two pieces: chaining short lines of dialogue repeated them caption after caption.
            if earlier and not continues.joined and len(earlier) + 1 + len(text) <= MERGE_MAX_CHARS:
                return f"{earlier} {text}", continues.segment.seq
        return text, None

    # ------------------------------------------------------------ final captions

    async def _process(self, job: _Job, continues: Optional[_Job]) -> Optional[_Partial]:
        segment = job.segment
        deadline = segment.ended_at + self._s.subtitle_deadline_s
        try:
            t0 = time.monotonic()
            text = await self._asr.transcribe(segment.audio, self._s.their_language, deadline)
            asr_s = time.monotonic() - t0
            if not is_meaningful(text):
                return None
            original, replaces = await self._join(continues, text)
            job.joined = replaces is not None
            job.transcript.set_result(original)
        finally:
            if not job.transcript.done():  # noise, failure or shutdown: the next one starts afresh
                job.transcript.set_result(None)

        times = dict(start=segment.started_at, end=segment.speech_end)
        if self._s.their_language == self._s.my_language:
            caption = Caption("in", original, original, segment.seq, replaces=replaces, **times)
            return _Partial(segment, caption, asr_s, time.monotonic(), None)
        # Translate now, concurrently; the ordered sink shows the transcript meanwhile.
        translation = asyncio.create_task(self._translate(original, deadline))
        caption = Caption("in", original, "", segment.seq, pending=True, replaces=replaces, **times)
        return _Partial(segment, caption, asr_s, time.monotonic(), translation)

    async def _translate(self, text: str, deadline: float) -> _Translation:
        t0 = time.monotonic()
        translated = tidy(await self._client.translate(text, self._s.my_language, self._s.subtitle_script, deadline))
        now = time.monotonic()
        return _Translation(translated, now - t0, now)

    async def _show(self, p: _Partial) -> None:
        self._ui.show_caption(p.caption)
        if p.translation is None:
            self._log(p, p.caption.translated, 0.0, p.shown_at)
            return
        try:
            result = await p.translation
        except asyncio.CancelledError:
            p.translation.cancel()
            raise
        except Exception as exc:
            self._ui.show_caption(dataclasses.replace(p.caption, pending=False))  # keep the transcript
            self._on_error(exc)
            return
        self._ui.show_caption(dataclasses.replace(p.caption, translated=result.text, pending=False))
        self._log(p, result.text, result.mt_s, result.ready_at)

    def _log(self, p: _Partial, translated: str, mt_s: float, ready_at: float) -> None:
        c, seg = p.caption, p.segment
        merged = f" continues #{c.replaces} |" if c.replaces else ""
        # From the VAD cut: partial = transcript ready, e2e = translation ready (incl. rate-limit
        # waits and retries).
        log.info("in #%d %.1fs audio | asr %.2fs mt %.2fs | e2e %.2fs | partial %.2fs |%s %s -> %s",
                 seg.seq, seg.duration_s, p.asr_s, mt_s, ready_at - seg.ended_at,
                 p.shown_at - seg.ended_at, merged, c.original, translated)

    def _on_error(self, exc: Exception) -> None:
        if isinstance(exc, BodhanError) and exc.code == "deadline_exceeded":
            log.warning("Dropped a stale subtitle")
            return
        log.error("Subtitle failed: %s", exc)
        self._ui.set_status(f"Subtitle failed: {getattr(exc, 'code', type(exc).__name__)}")

    # ------------------------------------------------------------- live captions

    def _maybe_live(self, previous: Optional[_Job], runner: InOrder) -> None:
        now = time.monotonic()
        if self._live_task is not None and not self._live_task.done():
            return
        if now - self._live_started < self._s.live_caption_interval_s or not self._spare_translations():
            return
        current = self._seg.current()
        if current is None or current.duration_s < LIVE_FIRST_S:
            return
        self._live_started = now
        continues = previous if self._continues(previous, current) else None
        self._live_task = asyncio.create_task(self._live(current, continues, runner))

    def _spare_translations(self) -> bool:
        if self._s.their_language == self._s.my_language:
            return True  # live captions need no translation
        reserve = max(LIVE_TRANSLATE_RESERVE, self._s.limits.rpm_for("translate") // 2)
        return self._client.headroom("translate") > reserve

    async def _live(self, current: Segment, continues: Optional[_Job], runner: InOrder) -> None:
        def stale() -> bool:  # its sentence ended (the final caption takes over) or one is still due
            return self._emitted >= current.seq or not runner.idle

        try:
            t0 = time.monotonic()
            text = await self._asr.transcribe(current.audio, self._s.their_language, background=True)
            t1 = time.monotonic()
            if not is_meaningful(text):
                return
            original, replaces = await self._join(continues, text)
            if stale() or not self._spare_translations():
                return
            if self._s.their_language == self._s.my_language:
                translated = original
            else:
                translated = tidy(await self._client.translate(
                    original, self._s.my_language, self._s.subtitle_script,
                    time.monotonic() + LIVE_TRANSLATE_TIMEOUT_S))
            if stale():
                return
            self._ui.show_caption(Caption("in", original, translated, current.seq, replaces=replaces, live=True))
            log.info("in #%d live %.1fs so far | asr %.2fs mt %.2fs | %s -> %s", current.seq,
                     current.duration_s, t1 - t0, time.monotonic() - t1, original, translated)
        except Busy:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # provisional: never worth an error on screen
            log.debug("Live caption skipped: %s", exc)
