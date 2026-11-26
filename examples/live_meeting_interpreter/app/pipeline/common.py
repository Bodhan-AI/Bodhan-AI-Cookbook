from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caption:
    """One subtitle. The same `seq` is shown twice for subtitles: first with `pending=True`
    (transcript only, translation on its way), then complete. The UI updates it in place."""

    direction: Literal["in", "out"]  # in = they spoke, out = you spoke
    original: str
    translated: str
    seq: int = 0                     # utterance number within its direction (0 = unnumbered)
    pending: bool = False            # translation not here yet
    replaces: Optional[int] = None   # seq of the caption this one continues and supersedes
    live: bool = False               # provisional: the speaker is still talking (local ASR only)
    # When the speech started and stopped (time.monotonic()), for the session record's timings.
    start: Optional[float] = field(default=None, compare=False)
    end: Optional[float] = field(default=None, compare=False)

    @property
    def final(self) -> bool:
        return not self.pending and not self.live


class UI(Protocol):
    """Thread-safe surface the pipelines talk to (implemented by ui/overlay.py)."""

    def show_caption(self, caption: Caption) -> None: ...
    def set_status(self, text: str, active: bool = False) -> None: ...


class InOrder:
    """Run jobs concurrently (bounded by the rate limiter) but deliver results in submission order.

    Subtitles and spoken translations must never appear out of order, even if a
    later, shorter utterance finishes first.
    """

    def __init__(self, sink: Callable[[Any], Awaitable[None]],
                 on_error: Callable[[Exception], None]) -> None:
        self._sink = sink
        self._on_error = on_error
        self._queue: asyncio.Queue[asyncio.Future] = asyncio.Queue()
        self._outstanding = 0  # submitted but not yet fully delivered (or dropped)

    @property
    def idle(self) -> bool:
        """True when every submitted job has been delivered: nothing is waiting to be shown."""
        return self._outstanding == 0

    def submit(self, coro: Awaitable[Any]) -> None:
        self._outstanding += 1
        self._queue.put_nowait(asyncio.ensure_future(coro))

    async def run(self) -> None:
        while True:
            fut = await self._queue.get()
            try:
                await self._deliver(fut)
            finally:
                self._outstanding -= 1

    async def _deliver(self, fut: asyncio.Future) -> None:
        try:
            result = await fut
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._on_error(exc)
            return
        if result is not None:
            try:
                await self._sink(result)
            except Exception:
                log.exception("Result sink failed")
