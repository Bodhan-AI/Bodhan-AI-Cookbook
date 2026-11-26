"""You speak -> they hear: mic (while push-to-talk held) -> VAD -> Indic-Transcribe
-> Indic-Translate -> Indic-Speak -> VB-Cable -> Meet/Zoom microphone.

Segments are cut at your pauses WHILE the key is held, so the first sentence is
already being translated while you say the second one.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from app.asr import Transcriber
from app.audio.playback import Player
from app.audio.vad import Segment, Segmenter
from app.bodhan.client import BodhanClient, BodhanError
from app.config import Settings
from app.pipeline.common import UI, Caption, InOrder
from app.pipeline.text import is_meaningful, split_for_streaming, tidy

log = logging.getLogger(__name__)
FLUSH = object()  # queued when push-to-talk is released


@dataclass
class Utterance:
    caption: Caption
    first: bytes                      # audio of the first chunk, ready to play
    rest: list[asyncio.Task[bytes]]   # later chunks, still being synthesised


class OutgoingInterpreter:
    def __init__(self, settings: Settings, client: BodhanClient, asr: Transcriber, ui: UI, segmenter: Segmenter,
                 meeting: Player, monitor: Optional[Player], tts_playing: threading.Event) -> None:
        self._s = settings
        self._client = client
        self._asr = asr
        self._ui = ui
        self._seg = segmenter
        self._meeting = meeting
        self._monitor = monitor
        self._tts_playing = tts_playing

    async def run(self, mic: asyncio.Queue, stop: asyncio.Event) -> None:
        runner = InOrder(self._play, self._on_error)
        consumer = asyncio.create_task(runner.run())
        try:
            while not stop.is_set():
                try:
                    item = await asyncio.wait_for(mic.get(), 0.25)
                except asyncio.TimeoutError:
                    continue
                segments = self._seg.flush() if item is FLUSH else self._seg.push(item)
                for segment in segments:
                    runner.submit(self._process(segment))
        finally:
            consumer.cancel()

    async def _process(self, segment: Segment) -> Optional[Utterance]:
        s = self._s
        deadline = segment.ended_at + s.interpret_deadline_s
        t0 = time.monotonic()
        text = await self._asr.transcribe(segment.audio, s.my_language, deadline)
        if not is_meaningful(text):
            return None
        t1 = time.monotonic()
        translated = (text if s.my_language == s.their_language
                      else tidy(await self._client.translate(text, s.their_language, "native", deadline)))
        t2 = time.monotonic()
        chunks = split_for_streaming(translated)
        if not chunks:
            return None
        # Start every chunk now (the rate limiter still bounds concurrency); playback begins as
        # soon as the first is ready and later ones are synthesised while earlier ones play.
        tasks = [asyncio.create_task(self._client.speak(chunk, s.their_language, s.tts_voice, s.tts_style, deadline))
                 for chunk in chunks]
        try:
            first = await tasks[0]
        except BaseException:
            for task in tasks[1:]:
                task.cancel()
            raise
        now = time.monotonic()
        # tts: until the FIRST chunk's audio is ready. e2e: from the VAD cut / key release to
        # the moment the listener can start hearing the translation.
        log.info("out #%d %.1fs audio | asr %.2fs mt %.2fs tts %.2fs | e2e %.2fs | %d part(s) | %s -> %s",
                 segment.seq, segment.duration_s, t1 - t0, t2 - t1, now - t2, now - segment.ended_at,
                 len(chunks), text, translated)
        caption = Caption("out", text, translated, segment.seq, start=segment.started_at, end=segment.speech_end)
        return Utterance(caption, first, tasks[1:])

    async def _play(self, utt: Utterance) -> None:
        self._ui.show_caption(utt.caption)
        self._tts_playing.set()  # mutes loopback capture if the monitor plays on your speakers
        try:
            await self._play_wav(utt.first)
            for i, task in enumerate(utt.rest, start=2):
                try:
                    wav = await task
                except Exception as exc:  # the rest of this utterance would make no sense without it
                    log.error("Interpretation part %d failed, skipping the rest: %s", i, exc)
                    self._ui.set_status("Part of the translation failed and was skipped")
                    return
                await self._play_wav(wav)
        finally:
            for task in utt.rest:
                task.cancel()  # no-op once finished; stops leftovers on error or shutdown
            self._tts_playing.clear()

    async def _play_wav(self, wav: bytes) -> None:
        jobs = [asyncio.to_thread(self._meeting.play_wav, wav)]
        if self._monitor:
            jobs.append(asyncio.to_thread(self._monitor.play_wav, wav))
        await asyncio.gather(*jobs)

    def _on_error(self, exc: Exception) -> None:
        if isinstance(exc, BodhanError) and exc.code == "deadline_exceeded":
            log.warning("Dropped a stale interpretation")
            self._ui.set_status("Translation was too slow and was skipped")
            return
        log.error("Interpretation failed: %s", exc)
        self._ui.set_status(f"Interpretation failed: {getattr(exc, 'code', type(exc).__name__)}")
