"""Voice activity detection and utterance segmentation.

Bodhan ASR is batch-only (max 30 s, billed per second), so we cut the live
stream into utterances: start on speech, end on a pause, and force a cut
before the 30 s limit. After `soft_max_s` of continuous speech, a much shorter
pause is enough to cut, which keeps long monologues from delaying captions.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from app.audio.wavutil import SAMPLE_RATE
from app.config import VadSettings

FRAME = 512                       # Silero VAD frame at 16 kHz
FRAME_MS = FRAME * 1000 / SAMPLE_RATE  # 32 ms


class SileroVAD:
    """Callable: 512-sample float32 frame -> speech probability."""

    def __init__(self) -> None:
        import torch  # imported lazily so tests don't need torch
        from silero_vad import load_silero_vad

        self._torch = torch
        self._model = load_silero_vad()
        torch.set_num_threads(1)

    def __call__(self, frame: np.ndarray) -> float:
        with self._torch.no_grad():
            return float(self._model(self._torch.from_numpy(frame), SAMPLE_RATE).item())


@dataclass
class Segment:
    seq: int
    audio: np.ndarray     # mono float32 @ 16 kHz
    started_at: float     # monotonic clock
    ended_at: float       # when the VAD cut it
    trailing_s: float = 0.0  # silence before the cut: speech stopped at ended_at - trailing_s

    @property
    def speech_end(self) -> float:
        return self.ended_at - self.trailing_s

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SAMPLE_RATE


class Segmenter:
    def __init__(
        self,
        vad: Callable[[np.ndarray], float],
        threshold: float = 0.5,
        min_speech_ms: int = 300,
        end_silence_ms: int = 600,
        soft_max_s: float = 8.0,
        soft_end_silence_ms: int = 200,
        hard_max_s: float = 25.0,
        pre_roll_ms: int = 300,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vad = vad
        self._on = threshold
        self._off = max(0.05, threshold - 0.15)  # hysteresis
        self._min_voiced = int(min_speech_ms / FRAME_MS)
        self._end_frames = max(1, int(end_silence_ms / FRAME_MS))
        self._soft_end_frames = max(1, int(soft_end_silence_ms / FRAME_MS))
        self._soft_max_frames = int(soft_max_s * 1000 / FRAME_MS)
        self._hard_max_frames = int(hard_max_s * 1000 / FRAME_MS)
        self._clock = clock
        self._pre_roll: deque[np.ndarray] = deque(maxlen=max(1, int(pre_roll_ms / FRAME_MS)))
        self._remainder = np.zeros(0, dtype=np.float32)
        self._seq = 0
        self._reset_utterance()

    def _reset_utterance(self) -> None:
        self._speaking = False
        self._frames: list[np.ndarray] = []
        self._voiced = 0
        self._silence_run = 0
        self._speech_run = 0
        self._started_at: Optional[float] = None

    @property
    def in_speech(self) -> bool:
        return self._speaking

    def push(self, samples: np.ndarray) -> list[Segment]:
        """Feed any number of samples; returns utterances that completed."""
        data = np.concatenate([self._remainder, samples.astype(np.float32, copy=False)])
        n = len(data) // FRAME * FRAME
        self._remainder = data[n:]
        out: list[Segment] = []
        for i in range(0, n, FRAME):
            seg = self._step(data[i:i + FRAME])
            if seg:
                out.append(seg)
        return out

    def current(self) -> Optional[Segment]:
        """The utterance in progress so far (for live captions), or None when nobody is speaking.

        Its seq is the one it will get when it ends (an utterance this long is always emitted).
        """
        if not self._speaking or not self._frames:
            return None
        return Segment(self._seq + 1, np.concatenate(self._frames),
                       self._started_at if self._started_at is not None else self._clock(), self._clock())

    def flush(self) -> list[Segment]:
        """Close the current utterance now (e.g. push-to-talk released)."""
        seg = self._emit() if self._speaking else None
        self._remainder = np.zeros(0, dtype=np.float32)
        self._pre_roll.clear()
        return [seg] if seg else []

    def _step(self, frame: np.ndarray) -> Optional[Segment]:
        prob = self._vad(frame)
        if not self._speaking:
            self._pre_roll.append(frame)
            self._speech_run = self._speech_run + 1 if prob >= self._on else 0
            if self._speech_run >= 2:  # ~64 ms of speech to trigger
                self._speaking = True
                self._started_at = self._clock()
                self._frames = list(self._pre_roll)
                self._voiced = self._speech_run
                self._pre_roll.clear()
            return None

        self._frames.append(frame)
        if prob >= self._off:
            self._voiced += 1
            self._silence_run = 0
        else:
            self._silence_run += 1
        needed = self._soft_end_frames if len(self._frames) >= self._soft_max_frames else self._end_frames
        if self._silence_run >= needed or len(self._frames) >= self._hard_max_frames:
            return self._emit()
        return None

    def _emit(self) -> Optional[Segment]:
        frames, voiced, started = self._frames, self._voiced, self._started_at
        trailing = self._silence_run
        self._reset_utterance()
        if voiced < self._min_voiced or not frames:
            return None
        # keep ~200 ms of trailing silence, trim the rest (Bodhan bills per second)
        keep_tail = int(200 / FRAME_MS)
        if trailing > keep_tail:
            frames = frames[: len(frames) - (trailing - keep_tail)]
        self._seq += 1
        return Segment(self._seq, np.concatenate(frames), started or self._clock(), self._clock(),
                       trailing * FRAME_MS / 1000)


def make_segmenter(v: VadSettings, *, push_to_talk: bool = False,
                   vad: Optional[Callable[[np.ndarray], float]] = None,
                   clock: Callable[[], float] = time.monotonic) -> Segmenter:
    """The segmenter the app uses, built from .env settings (shared by app.main and tools)."""
    return Segmenter(
        vad if vad is not None else SileroVAD(),
        threshold=v.threshold,
        end_silence_ms=v.end_silence_ms,
        soft_max_s=v.soft_max_s,
        hard_max_s=v.hard_max_s,
        # while you hold the key you are clearly talking: cut sentences a bit sooner
        min_speech_ms=250 if push_to_talk else 300,
        clock=clock,
    )


def replay(audio: np.ndarray, build: Callable[[Callable[[], float]], Segmenter]) -> list[Segment]:
    """Run a recording through a fresh segmenter, offline.

    `build(clock)` must return a Segmenter using `clock`. The clock reads recording time
    (seconds of audio pushed so far), so `Segment.ended_at` is the point in the recording
    where the app would have sent the utterance to ASR. An utterance still open at
    the end of the recording is flushed.
    """
    pushed = 0
    segmenter = build(lambda: pushed / SAMPLE_RATE)
    out: list[Segment] = []
    for i in range(0, len(audio), FRAME):
        block = audio[i:i + FRAME]
        pushed = i + len(block)
        out.extend(segmenter.push(block))
    out.extend(segmenter.flush())
    return out
