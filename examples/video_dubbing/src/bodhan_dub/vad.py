"""Voice-activity detection: where in the audio is someone speaking?

Indic-Transcribe returns text without timestamps, so these regions are the pipeline's
clock: every segment's start and end time comes from here.

Two steps:
  1. Score each 32 ms frame with a speech probability (Silero VAD, or an energy
     fallback when Silero's native library is unavailable on this platform).
  2. Turn probabilities into regions with hysteresis, then clean them up:
     bridge short pauses, drop blips, pad the edges.
"""

from __future__ import annotations

import logging
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512  # Silero's native window at 16 kHz
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE  # 0.032 s


@dataclass(frozen=True)
class Region:
    start: float  # seconds
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class VadParams:
    # Hysteresis: enter speech above `on`, leave it only below `off`, so a frame that
    # wobbles around one threshold doesn't chop a word into pieces.
    on_threshold: float = 0.5
    off_threshold: float = 0.35
    # Pauses shorter than this are breaths inside a phrase, not boundaries.
    min_silence_s: float = 0.30
    # Clicks and coughs shorter than this are not speech worth dubbing.
    min_speech_s: float = 0.25
    # Silero fires slightly late on soft onsets and early on trailing consonants.
    pad_s: float = 0.10


@dataclass(frozen=True)
class VadResult:
    regions: list[Region]
    probs: np.ndarray  # one speech probability per FRAME_S frame
    backend: str  # "silero" or "energy"
    duration_s: float


def load_wav(path: Path) -> np.ndarray:
    """Read a mono 16 kHz PCM16 WAV (what media.extract_audio writes) as float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        if (wf.getnchannels(), wf.getframerate(), wf.getsampwidth()) != (1, SAMPLE_RATE, 2):
            raise ValueError(f"{path}: expected mono 16 kHz PCM16 WAV")
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _frames(samples: np.ndarray) -> np.ndarray:
    """Split into whole FRAME_SAMPLES frames, zero-padding the tail."""
    n = int(np.ceil(len(samples) / FRAME_SAMPLES)) if len(samples) else 0
    padded = np.zeros(n * FRAME_SAMPLES, dtype=np.float32)
    padded[: len(samples)] = samples
    return padded.reshape(n, FRAME_SAMPLES)


def silero_probabilities(samples: np.ndarray) -> np.ndarray:
    from silero_vad_lite import SileroVAD  # native library; may be missing on some platforms

    vad = SileroVAD(SAMPLE_RATE)
    return np.array([vad.process(frame) for frame in _frames(samples)], dtype=np.float32)


def energy_probabilities(samples: np.ndarray) -> np.ndarray:
    """Fallback: loudness above the recording's own noise floor, mapped to [0, 1].

    Relative to this file's floor (10th-percentile frame level), so it adapts to
    quiet and loud recordings alike. Weaker than Silero on music, which is why the
    backend is logged and reported.
    """
    frames = _frames(samples)
    if len(frames) == 0:
        return np.zeros(0, dtype=np.float32)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    db = 20 * np.log10(rms + 1e-10)
    floor = np.percentile(db, 10)
    # 6 dB above the floor counts as silence, 18 dB above as certain speech.
    probs = np.clip((db - floor - 6.0) / 12.0, 0.0, 1.0)
    probs[db < -60] = 0.0  # digital silence is never speech, whatever the floor
    return probs.astype(np.float32)


def probabilities_to_regions(probs: np.ndarray, params: VadParams, duration_s: float) -> list[Region]:
    """Hysteresis, then bridge short pauses, drop blips and pad. Pure and deterministic."""
    raw: list[list[float]] = []
    speaking = False
    for i, p in enumerate(probs):
        if not speaking and p >= params.on_threshold:
            speaking = True
            raw.append([i * FRAME_S, (i + 1) * FRAME_S])
        elif speaking:
            if p < params.off_threshold:
                speaking = False
            else:
                raw[-1][1] = (i + 1) * FRAME_S

    bridged: list[list[float]] = []
    for start, end in raw:
        if bridged and start - bridged[-1][1] < params.min_silence_s:
            bridged[-1][1] = end
        else:
            bridged.append([start, end])

    kept = [r for r in bridged if r[1] - r[0] >= params.min_speech_s]

    regions: list[Region] = []
    for start, end in kept:
        start = max(0.0, start - params.pad_s)
        end = min(duration_s, end + params.pad_s)
        if regions and start <= regions[-1].end:  # padding made neighbours touch
            regions[-1] = Region(regions[-1].start, end)
        else:
            regions.append(Region(start, end))
    return [Region(round(r.start, 3), round(r.end, 3)) for r in regions]


def quietest_point(probs: np.ndarray, lo: float, hi: float, smooth_frames: int = 5) -> float:
    """Time in [lo, hi] where speech is least likely: the best place for a forced cut.

    Smoothed over a few frames so we land in a micro-pause, not a single noisy dip.
    """
    i_lo, i_hi = int(lo / FRAME_S), int(np.ceil(hi / FRAME_S))
    window = probs[i_lo:i_hi]
    if len(window) == 0:
        return (lo + hi) / 2
    if len(window) >= smooth_frames:
        window = np.convolve(window, np.ones(smooth_frames) / smooth_frames, mode="same")
    return min(max((i_lo + int(np.argmin(window)) + 0.5) * FRAME_S, lo), hi)


def detect_speech(samples: np.ndarray, params: VadParams | None = None, backend: str = "auto") -> VadResult:
    params = params or VadParams()
    duration_s = len(samples) / SAMPLE_RATE
    used = backend
    if backend in ("auto", "silero"):
        try:
            probs = silero_probabilities(samples)
            used = "silero"
        except (ImportError, OSError) as exc:
            if backend == "silero":
                raise
            log.warning("silero VAD unavailable, using energy fallback", extra={"stage": "vad", "error": str(exc)})
            used = "energy"
    if used == "energy":
        probs = energy_probabilities(samples)
    regions = probabilities_to_regions(probs, params, duration_s)
    return VadResult(regions=regions, probs=probs, backend=used, duration_s=duration_s)
