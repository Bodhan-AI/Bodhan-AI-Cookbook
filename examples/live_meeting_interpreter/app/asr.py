"""Speech-to-text backends. Both run Bodhan's Indic-Transcribe:

- HostedTranscriber: Bodhan's API (default). Faster per sentence, billed per second, rate-limited.
- LocalTranscriber: the open Indic-Transcribe Core weights on this PC's NVIDIA GPU
  (ASR_BACKEND=local). Same transcripts; no billing or rate limits; ~0.16 s of work per second
  of audio on an RTX 3050 Ti, so slower than hosted for a whole sentence.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Protocol

import numpy as np

from app.audio.wavutil import SAMPLE_RATE, encode_wav
from app.bodhan.client import BodhanClient, BodhanError

log = logging.getLogger(__name__)

WEIGHTS_HELP = ('Download them with: hf download bodhan-ai/indic-transcribe-core --exclude "nemo/*" '
                "--local-dir models/indic-transcribe-core (see README)")


class Busy(Exception):
    """A background request was skipped because the model was busy with other work."""


class Transcriber(Protocol):
    async def transcribe(self, audio: np.ndarray, language: str, deadline: Optional[float] = None,
                         *, background: bool = False) -> str:
        """16 kHz mono float32 audio (< 30 s) -> text. Raises BodhanError("deadline_exceeded")
        when the result would arrive after `deadline` (time.monotonic()).

        background=True (live captions): run only if the model is idle, else raise Busy at once,
        so provisional work never delays a final caption."""
        ...


class HostedTranscriber:
    def __init__(self, client: BodhanClient) -> None:
        self._client = client

    async def transcribe(self, audio: np.ndarray, language: str, deadline: Optional[float] = None,
                         *, background: bool = False) -> str:
        return await self._client.transcribe(encode_wav(audio), language, deadline)


class LocalTranscriber:
    """One utterance at a time on the GPU, in a worker thread so the event loop never blocks.

    Both directions share one instance (one model in VRAM); utterances queue in arrival order.
    """

    def __init__(self, model: Callable[[np.ndarray, str], str],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._model = model
        self._clock = clock
        self._queue = asyncio.Lock()   # order and deadline checks, on the event loop
        self._gpu = threading.Lock()   # also held by a call that outlives a cancelled awaiter

    async def transcribe(self, audio: np.ndarray, language: str, deadline: Optional[float] = None,
                         *, background: bool = False) -> str:
        if background and self._queue.locked():
            raise Busy("speech model busy")  # never queue ahead of a final transcription
        async with self._queue:
            if deadline is not None and self._clock() > deadline:
                raise BodhanError(0, "deadline_exceeded", "local transcription would be stale")
            return await asyncio.to_thread(self._run, audio, language)

    def _run(self, audio: np.ndarray, language: str) -> str:
        with self._gpu:
            return str(self._model(audio, language)).strip()

    @classmethod
    def load(cls, model_dir: str, warmup_language: str) -> "LocalTranscriber":
        """Load the weights onto the GPU and warm up. Blocking, 5-15 s: call via asyncio.to_thread."""
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("ASR_BACKEND=local needs an NVIDIA GPU and the CUDA build of torch (see README).")
        path = Path(model_dir).resolve()
        if not (path / "indic_transcribe.py").is_file() or not (path / "model.safetensors").is_file():
            raise RuntimeError(f"No Indic-Transcribe weights in {path}. {WEIGHTS_HELP}")
        # The model's own wrapper imports its sibling files as flat modules, so its folder
        # must be importable. It loads safetensors + SentencePiece only (reviewed 2026-11-26).
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
        from indic_transcribe import IndicTranscribe  # type: ignore[import-not-found]

        t0 = time.monotonic()
        asr = IndicTranscribe.from_pretrained(str(path), device="cuda")

        def model(audio: np.ndarray, language: str) -> str:
            return asr(audio, lang=language)

        model(np.zeros(SAMPLE_RATE, dtype=np.float32), warmup_language)  # CUDA kernels + allocator
        log.info("Local Indic-Transcribe ready on %s in %.1fs (%.2f GiB of VRAM)",
                 torch.cuda.get_device_name(0), time.monotonic() - t0, torch.cuda.memory_allocated() / 2**30)
        return cls(model)
