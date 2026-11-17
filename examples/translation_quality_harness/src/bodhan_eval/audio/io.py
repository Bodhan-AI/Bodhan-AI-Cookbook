"""Audio decode/encode. WAV/FLAC/OGG (and MP3 with recent libsndfile) via soundfile; ffmpeg as fallback."""

from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass
class Audio:
    samples: np.ndarray  # float32 mono in [-1, 1]
    rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate if self.rate else 0.0


class AudioDecodeError(Exception):
    pass


def _to_mono(data: np.ndarray) -> np.ndarray:
    if data.ndim == 2:
        data = data.mean(axis=1)
    return data.astype(np.float32, copy=False)


def decode(data: bytes) -> Audio:
    try:
        samples, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
        return Audio(_to_mono(samples), int(rate))
    except Exception as exc:  # noqa: BLE001 - soundfile raises several unrelated types
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise AudioDecodeError(f"cannot decode audio ({exc}); install ffmpeg for other formats") from exc
        proc = subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-f", "wav", "-ac", "1", "pipe:1"], input=data, capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise AudioDecodeError(proc.stderr.decode(errors="replace")[:300]) from exc
        samples, rate = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=False)
        return Audio(_to_mono(samples), int(rate))


def load(path: str | Path) -> Audio:
    return decode(Path(path).read_bytes())


def encode_wav(audio: Audio) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.clip(audio.samples, -1.0, 1.0), audio.rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def concat(clips: list[Audio], gap_s: float = 0.0) -> Audio:
    if not clips:
        return Audio(np.zeros(0, dtype=np.float32), 24000)
    rate = clips[0].rate
    parts = []
    gap = np.zeros(int(gap_s * rate), dtype=np.float32)
    for k, clip in enumerate(clips):
        samples = clip.samples if clip.rate == rate else resample(clip, rate).samples
        parts.append(samples)
        if gap_s and k < len(clips) - 1:
            parts.append(gap)
    return Audio(np.concatenate(parts).astype(np.float32), rate)


def resample(audio: Audio, rate: int) -> Audio:
    if audio.rate == rate or len(audio.samples) == 0:
        return Audio(audio.samples, rate)
    n = int(round(len(audio.samples) * rate / audio.rate))
    x_old = np.linspace(0.0, 1.0, len(audio.samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n, endpoint=False)
    return Audio(np.interp(x_new, x_old, audio.samples).astype(np.float32), rate)


def slice_seconds(audio: Audio, start: float, end: float) -> Audio:
    a, b = int(start * audio.rate), int(end * audio.rate)
    return Audio(audio.samples[a:b], audio.rate)
