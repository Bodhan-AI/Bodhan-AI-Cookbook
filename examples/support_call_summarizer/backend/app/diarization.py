"""Speaker diarization ("who spoke when") — an optional companion model.

Bodhan models do not identify speakers, so this step uses a separate model. A
diarizer returns speaker turns; ``segmenter.split_by_turns`` then cuts each turn
at pauses so every piece belongs to one speaker.

``nemotron`` runs NVIDIA Nemotron-3-Diarization locally through Hugging Face
Transformers. It needs the packages in ``requirements-diarization.txt`` and a
CUDA GPU with at least ``DIARIZATION_MIN_VRAM_GB`` of memory; without them it
turns itself off at startup (with a log line saying why) and calls are split at
pauses as usual.

To add a provider, implement ``Diarizer`` and register it in ``build_diarizer``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from . import audio
from .config import Settings
from .segmenter import SpeakerTurn

log = logging.getLogger(__name__)


class DiarizationError(Exception):
    pass


class Diarizer(Protocol):
    async def diarize(self, wav_path: Path, duration: float) -> list[SpeakerTurn]:
        """Return speaker turns for a 16 kHz mono WAV file. Turns may overlap."""
        ...

    async def aclose(self) -> None: ...


def check_gpu(min_vram_gb: float) -> str | None:
    """Return why the local GPU cannot run the speaker model, or None when it can."""
    try:
        import torch
        import transformers  # noqa: F401
    except ImportError:
        return "PyTorch/Transformers are not installed (pip install -r requirements-diarization.txt)"
    if not torch.cuda.is_available():
        return "no CUDA-capable NVIDIA GPU was found (or PyTorch is a CPU-only build)"
    props = torch.cuda.get_device_properties(0)
    vram_gb = props.total_memory / 2**30
    if vram_gb < min_vram_gb:
        return f"{props.name} has {vram_gb:.1f} GB of memory; DIARIZATION_MIN_VRAM_GB is {min_vram_gb:g}"
    return None


class NemotronDiarizer:
    """NVIDIA Nemotron-3-Diarization (up to 8 speakers) on the first CUDA GPU.

    The model's offline mode splits a recording into ~30 s chunks with a
    speaker cache, so GPU memory stays around 0.5 GB whatever the call length.
    """

    def __init__(self, model_id: str):
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor

        self._torch = torch
        self._processor = AutoProcessor.from_pretrained(model_id)
        self._model = AutoModelForAudioFrameClassification.from_pretrained(model_id).to("cuda").eval()
        self._lock = asyncio.Lock()  # one call at a time on the GPU; each takes seconds

    async def diarize(self, wav_path: Path, duration: float) -> list[SpeakerTurn]:
        async with self._lock:
            return await asyncio.to_thread(self._run, wav_path)

    def _run(self, wav_path: Path) -> list[SpeakerTurn]:
        samples = audio.read_wav(wav_path).astype(np.float32) / 32768
        inputs = self._processor(samples, sampling_rate=audio.SAMPLE_RATE).to("cuda")
        with self._torch.inference_mode():
            logits = self._model(**inputs).logits
        segments = self._processor.extract_speaker_dict(logits, inputs.attention_mask)[0]
        return [SpeakerTurn(s["Start"], s["End"], f"speaker_{s['Speaker']}") for s in segments]

    async def aclose(self) -> None:
        del self._model
        self._torch.cuda.empty_cache()


def build_diarizer(settings: Settings) -> Diarizer | None:
    """Return the configured diarizer, or None when it is off or this machine cannot run it."""
    provider = settings.diarization_provider.lower()
    if provider == "none":
        return None
    if provider != "nemotron":
        raise DiarizationError(
            f"Unknown DIARIZATION_PROVIDER {settings.diarization_provider!r}; use 'none' or 'nemotron'."
        )
    reason = check_gpu(settings.diarization_min_vram_gb)
    if reason:
        log.warning("Speaker diarization is off: %s.", reason)
        return None
    log.info("Loading %s on the GPU (the first run downloads ~400 MB)", settings.diarization_model)
    try:
        return NemotronDiarizer(settings.diarization_model)
    except Exception as exc:  # e.g. no network for the first download; the app still works without speakers
        log.warning("Speaker diarization is off: the model could not be loaded (%s: %s).", type(exc).__name__, exc)
        return None
