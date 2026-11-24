"""VAD-based segmentation — the spine of the pipeline.

Indic-Transcribe returns no timestamps and caps at 30s, so we *derive* the
timeline here. Segments are pause-bounded speech runs: cutting only at silence
means chunks stay <=30s AND almost never split a sentence, and the non-speech
complement is kept verbatim so music/effects/silence are never transcribed or
hallucinated into narration.

The windowing/merge/split logic is pure and operates on a boolean speech mask,
independent of which VAD backend produced it, so it is unit-testable in isolation.
"""

from __future__ import annotations

import numpy as np

from ..logging_setup import get_logger
from ..types import Segment

log = get_logger("segment")

FRAME_MS = 30
MAX_WINDOW_S = 29.0      # headroom under the hard 30s ASR cap
MERGE_GAP_S = 0.4        # merge speech runs separated by < this (keeps sentences whole)
MIN_SPEECH_S = 0.30      # discard blips shorter than this
PAD_S = 0.10             # tiny pad around a run so onsets/offsets aren't clipped


# ---------- pure geometry (unit-tested) ----------

def spans_from_mask(
    mask: np.ndarray,
    frame_ms: int = FRAME_MS,
    merge_gap_s: float = MERGE_GAP_S,
    min_speech_s: float = MIN_SPEECH_S,
) -> list[tuple[float, float]]:
    """Boolean per-frame speech mask -> merged (start, end) spans in seconds."""
    fs = frame_ms / 1000.0
    spans: list[list[float]] = []
    in_run = False
    for i, v in enumerate(mask):
        if v and not in_run:
            spans.append([i * fs, (i + 1) * fs])
            in_run = True
        elif v and in_run:
            spans[-1][1] = (i + 1) * fs
        else:
            in_run = False
    # merge small gaps
    merged: list[list[float]] = []
    for s, e in spans:
        if merged and s - merged[-1][1] <= merge_gap_s:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    # drop blips
    return [(s, e) for s, e in merged if (e - s) >= min_speech_s]


def cap_window(
    span: tuple[float, float],
    max_window_s: float = MAX_WINDOW_S,
    energy: np.ndarray | None = None,
    frame_ms: int = FRAME_MS,
) -> list[tuple[float, float]]:
    """Split a long speech run into <=max_window pieces, preferring low-energy cuts.

    Most runs are already short (bounded by pauses). A run with no internal pause
    that still exceeds the cap is split near the boundary at the quietest frame, so
    the cut lands on the softest point rather than mid-syllable.
    """
    s, e = span
    if e - s <= max_window_s:
        return [span]
    out: list[tuple[float, float]] = []
    cur = s
    fs = frame_ms / 1000.0
    while e - cur > max_window_s:
        target = cur + max_window_s
        cut = target
        if energy is not None:
            lo = int((cur + max_window_s * 0.6) / fs)
            hi = min(len(energy), int(target / fs))
            if hi > lo:
                cut = (lo + int(np.argmin(energy[lo:hi]))) * fs
                cut = min(max(cut, cur + max_window_s * 0.6), target)
        out.append((cur, cut))
        cur = cut
    out.append((cur, e))
    return out


def non_speech_spans(speech: list[tuple[float, float]], total: float) -> list[tuple[float, float]]:
    """Complement of speech spans over [0, total] — the music/effects/silence bed."""
    gaps: list[tuple[float, float]] = []
    prev = 0.0
    for s, e in speech:
        if s - prev > 1e-3:
            gaps.append((prev, s))
        prev = max(prev, e)
    if total - prev > 1e-3:
        gaps.append((prev, total))
    return gaps


# ---------- VAD backends ----------

def _energy_mask(samples: np.ndarray, sr: int, frame_ms: int = FRAME_MS) -> tuple[np.ndarray, np.ndarray]:
    """Adaptive energy VAD. Returns (speech_mask, per-frame RMS)."""
    flen = int(sr * frame_ms / 1000)
    if flen <= 0:
        return np.zeros(0, bool), np.zeros(0)
    n = len(samples) // flen
    if n == 0:
        return np.zeros(0, bool), np.zeros(0)
    frames = samples[: n * flen].reshape(n, flen)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    # Adaptive threshold: above the quiet floor, scaled toward the loud body.
    floor = np.percentile(rms, 15)
    peak = np.percentile(rms, 95)
    thresh = max(floor * 2.5, floor + 0.08 * (peak - floor), 1e-4)
    mask = rms > thresh
    return _hangover(mask), rms


def _hangover(mask: np.ndarray, pad_frames: int = 3) -> np.ndarray:
    """Bridge short dips so a single frame of quiet mid-word doesn't split a run."""
    out = mask.copy()
    last = -10_000
    for i, v in enumerate(mask):
        if v:
            last = i
        elif i - last <= pad_frames and last >= 0:
            out[i] = True
    return out


def _webrtc_mask(samples: np.ndarray, sr: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    import webrtcvad  # optional dependency (extra: vad)

    vad = webrtcvad.Vad(2)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    flen = int(sr * frame_ms / 1000) * 2
    out = []
    for i in range(0, len(pcm) - flen + 1, flen):
        out.append(vad.is_speech(pcm[i : i + flen], sr))
    return _hangover(np.array(out, dtype=bool))


# ---------- public entry ----------

def segment_audio(wav_path: str, backend: str = "energy") -> tuple[list[Segment], list[tuple[float, float]], float]:
    """Segment a 16k mono WAV into speech Segments + non-speech spans + duration."""
    from ..ffmpeg import read_wav

    samples, sr = read_wav(wav_path)
    total = len(samples) / sr if sr else 0.0

    energy = None
    if backend == "webrtc":
        mask = _webrtc_mask(samples, sr)
    elif backend == "silero":
        mask = _silero_mask(samples, sr)
    else:
        mask, energy = _energy_mask(samples, sr)

    spans = spans_from_mask(mask)
    windows: list[tuple[float, float]] = []
    for span in spans:
        windows.extend(cap_window(span, energy=energy))

    segments: list[Segment] = []
    for i, (s, e) in enumerate(windows):
        segments.append(
            Segment(
                index=i,
                start=round(max(0.0, s - PAD_S), 3),
                end=round(min(total, e + PAD_S), 3),
                is_speech=True,
            )
        )
    gaps = non_speech_spans([(seg.start, seg.end) for seg in segments], total)
    log.info(
        "segmented audio",
        extra={"event": "segmented", "extra": {"segments": len(segments),
                "non_speech_spans": len(gaps), "duration_s": round(total, 2)}},
    )
    return segments, gaps, total


def _silero_mask(samples: np.ndarray, sr: int) -> np.ndarray:  # pragma: no cover - optional
    from silero_vad import get_speech_timestamps, load_silero_vad

    model = load_silero_vad()
    ts = get_speech_timestamps(samples, model, sampling_rate=sr)
    frame_ms = FRAME_MS
    nframes = int(len(samples) / (sr * frame_ms / 1000)) + 1
    mask = np.zeros(nframes, dtype=bool)
    for seg in ts:
        a = int(seg["start"] / sr / (frame_ms / 1000))
        b = int(seg["end"] / sr / (frame_ms / 1000))
        mask[a : b + 1] = True
    return mask
