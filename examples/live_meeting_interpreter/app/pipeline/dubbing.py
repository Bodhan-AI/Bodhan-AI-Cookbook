"""Incoming dubbing (optional): speak their translated subtitles to you.

Off by default (DUB_INCOMING), switchable while running from the bar's right-click menu.
The subtitle pipeline is unchanged: DubbingUI hands each final "in" caption to the dubber, which
speaks it with Indic-Speak on DUB_DEVICE, one line at a time, in order.

Indic-Speak isn't streamed and takes ~0.07 s per character (3.6 s for a 48-character line,
measured 2026-11-26), about as long as the line takes to play. So the next line is synthesised
while the current one plays; otherwise every line waits for the previous one's synthesis and
playback, and the lag snowballs (line 2 of a test was heard 11.8 s late, line 3 dropped at 21 s).

Rules that keep it out of the way:
- It plays on its own device, never one the subtitle capture hears (Settings tests the device
  with a speech clip; app.main refuses the captured output and VB-Cable).
- It only uses spare speech requests: half the speech key's per-minute limit stays free for your
  interpreting, which must never stall because of dubbing. One request per line where possible.
- Late is worse than nothing: lines too far behind are skipped, and a merged caption replaces its
  fragment if that hasn't started playing.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from app.config import Settings
from app.pipeline.common import UI, Caption
from app.pipeline.text import split_for_streaming

log = logging.getLogger(__name__)
DUB_MAX_LAG_S = 8.0        # don't start synthesising a line whose speech ended longer ago than this
DUB_MAX_PLAY_LAG_S = 16.0  # ...or play one that has fallen this far behind by its turn
DUB_FIRST_CHARS = 80       # bigger first part than interpreting: one speech request per typical line
DUB_SPEAK_RESERVE = 3      # at least this many speech requests a minute stay free for interpreting
DUB_SPEAK_TIMEOUT_S = 8.0


class SpeechClient(Protocol):
    def headroom(self, model: str) -> int: ...
    async def speak(self, text: str, lang: str, voice: str, style: Optional[str] = None,
                    deadline: Optional[float] = None) -> bytes: ...


class WavPlayer(Protocol):
    def play_wav(self, wav: bytes) -> None: ...


@dataclass
class _Line:
    """A caption whose speech is being synthesised (one task per part)."""
    caption: Caption
    tasks: list[asyncio.Task[bytes]] = field(default_factory=list)
    started: float = 0.0

    def cancel(self) -> None:
        for task in self.tasks:
            task.cancel()  # no-op once done


class IncomingDubber:
    def __init__(self, settings: Settings, client: SpeechClient, player: WavPlayer, enabled: bool,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._s = settings
        self._client = client
        self._player = player
        self._clock = clock
        self._enabled = threading.Event()  # flipped from the Tk thread
        if enabled:
            self._enabled.set()
        self._queue: deque[Caption] = deque()
        self._next: Optional[_Line] = None  # synthesising while the current line plays
        self._wake = asyncio.Event()
        self.spoken: list[int] = []         # seqs spoken (for tests and the log)

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, on: bool) -> None:
        """Thread-safe. Turning it off drops whatever is waiting; a part being played finishes."""
        if on:
            self._enabled.set()
        else:
            self._enabled.clear()
        log.info("Speaking their translation: %s", "on" if on else "off")

    def offer(self, caption: Caption) -> None:
        """Called (on the event loop) for every caption shown; keeps the final translated ones."""
        if not self.enabled or not caption.final or caption.direction != "in" or not caption.translated:
            return
        if caption.replaces is not None:  # a merged sentence: its fragment needn't be said separately
            self._queue = deque(c for c in self._queue if c.seq != caption.replaces)
            if self._next is not None and self._next.caption.seq == caption.replaces:
                self._next.cancel()
                self._next = None
        self._queue.append(caption)
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                line = self._next or self._prepare_next()
                self._next = None
                if line is None:
                    self._wake.clear()
                    if not self._queue:
                        try:
                            await asyncio.wait_for(self._wake.wait(), 0.5)
                        except asyncio.TimeoutError:
                            pass
                    continue
                playing = asyncio.create_task(self._play(line))
                while not playing.done():
                    if self._next is None:
                        self._next = self._prepare_next()  # overlap its synthesis with this playback
                    await asyncio.wait({playing}, timeout=0.1)
                if not playing.cancelled() and playing.exception() is not None:
                    log.warning("Dub of #%d failed: %s", line.caption.seq, playing.exception())
        finally:
            if self._next is not None:
                self._next.cancel()

    def _reserve(self) -> int:
        return max(DUB_SPEAK_RESERVE, self._s.limits.rpm_for("speak") // 2)

    def _prepare_next(self) -> Optional[_Line]:
        while self._queue:
            if not self.enabled:
                self._queue.clear()
                return None
            line = self._prepare(self._queue.popleft())
            if line is not None:
                return line
        return None

    def _prepare(self, caption: Caption) -> Optional[_Line]:
        """Start synthesising a line, or skip it (too late, or no speech requests to spare)."""
        now = self._clock()
        lag = now - caption.end if caption.end is not None else 0.0
        if lag > DUB_MAX_LAG_S:
            log.info("Dub of #%d skipped: %.1fs behind", caption.seq, lag)
            return None
        parts = split_for_streaming(caption.translated, first_chars=DUB_FIRST_CHARS)
        if not parts:
            return None
        if self._client.headroom("speak") - len(parts) < self._reserve():
            log.info("Dub of #%d skipped: keeping speech requests for your interpreting", caption.seq)
            return None
        lang, voice, deadline = self._s.my_language, self._s.dub_voice, now + DUB_SPEAK_TIMEOUT_S
        tasks = [asyncio.create_task(self._client.speak(p, lang, voice, None, deadline)) for p in parts]
        return _Line(caption, tasks, now)

    async def _play(self, line: _Line) -> None:
        caption = line.caption
        try:
            for i, task in enumerate(line.tasks):
                wav = await task
                if not self.enabled:
                    return
                if i == 0:
                    now = self._clock()
                    lag = now - caption.end if caption.end is not None else 0.0
                    if lag > DUB_MAX_PLAY_LAG_S:
                        log.info("Dub of #%d skipped: %.1fs behind by its turn", caption.seq, lag)
                        return
                    log.info("dub #%d | speech %.2fs | heard %.1fs after they finished | %d part(s) | %s",
                             caption.seq, now - line.started, lag, len(line.tasks), caption.translated)
                await asyncio.to_thread(self._player.play_wav, wav)
            self.spoken.append(caption.seq)
        finally:
            line.cancel()  # stops leftovers when switched off, skipped or failing


class DubbingUI:
    """The pipelines' UI: passes everything on, and hands final captions to the dubber."""

    def __init__(self, ui: UI, dubber: IncomingDubber) -> None:
        self._ui = ui
        self._dubber = dubber

    def show_caption(self, caption: Caption) -> None:
        self._ui.show_caption(caption)
        self._dubber.offer(caption)

    def set_status(self, text: str, active: bool = False) -> None:
        self._ui.set_status(text, active)
