"""Audio I/O and signal statistics (numpy + stdlib ``wave``; ffmpeg only for non-WAV input).

Nothing here calls a model. Frame-level measurements use 20 ms frames:
  * level in dBFS, to find silences relative to this clip's own speech level
  * spectral flatness, to find stretches of broadband noise (flat spectrum, speech-loud)
  * clipping: samples at full scale in runs of 3 or more
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

FRAME_S = 0.02


def load_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """Return mono float32 samples in [-1, 1] and the sample rate."""
    path = Path(path)
    try:
        with wave.open(str(path)) as w:
            return _from_wave(w)
    except (wave.Error, EOFError):
        pass
    if not shutil.which("ffmpeg"):
        raise RuntimeError(f"{path.name}: not a PCM WAV file and ffmpeg is not installed")
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "wav", "-acodec", "pcm_s16le", "-ac", "1", "-"],
        capture_output=True,
        check=True,
    )
    with wave.open(io.BytesIO(proc.stdout)) as w:
        return _from_wave(w)


def load_audio_bytes(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        return _from_wave(w)


def _from_wave(w: wave.Wave_read) -> tuple[np.ndarray, int]:
    sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise wave.Error(f"unsupported sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def write_wav(path: str | Path, x: np.ndarray, sr: int) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(wav_bytes(x, sr))


def frames(x: np.ndarray, sr: int) -> np.ndarray:
    hop = max(1, int(sr * FRAME_S))
    n = len(x) // hop
    if n == 0:
        return np.zeros((1, max(1, len(x))), dtype=np.float32) if len(x) == 0 else x[None, :]
    return x[: n * hop].reshape(n, hop)


def frame_db(x: np.ndarray, sr: int) -> np.ndarray:
    f = frames(x, sr)
    rms = np.sqrt(np.mean(f.astype(np.float64) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-12)


def frame_flatness(x: np.ndarray, sr: int) -> np.ndarray:
    f = frames(x, sr) * np.hanning(frames(x, sr).shape[1])
    mag = np.abs(np.fft.rfft(f, axis=1)) + 1e-10
    return np.exp(np.mean(np.log(mag), axis=1)) / np.mean(mag, axis=1)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is True."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


@dataclass
class AudioStats:
    duration_s: float
    speech_level_db: float
    noise_floor_db: float
    active_s: float
    lead_silence_s: float
    trail_silence_s: float
    silences: list[tuple[float, float]] = field(default_factory=list)  # internal, >= 0.3 s
    noise_runs: list[tuple[float, float]] = field(default_factory=list)
    clip_ratio: float = 0.0
    clip_regions: list[tuple[float, float]] = field(default_factory=list)

    @property
    def longest_silence(self) -> tuple[float, float] | None:
        return max(self.silences, key=lambda s: s[1] - s[0], default=None)

    def summary(self) -> dict:
        ls = self.longest_silence
        return {
            "duration_s": round(self.duration_s, 3),
            "active_speech_s": round(self.active_s, 3),
            "lead_silence_s": round(self.lead_silence_s, 3),
            "trail_silence_s": round(self.trail_silence_s, 3),
            "longest_internal_silence_s": round(ls[1] - ls[0], 3) if ls else 0.0,
            "clip_ratio": round(self.clip_ratio, 5),
            "noise_floor_db": round(self.noise_floor_db, 1),
            "speech_level_db": round(self.speech_level_db, 1),
            "noise_s": round(sum(b - a for a, b in self.noise_runs), 3),
        }


def silence_threshold(db: np.ndarray) -> float:
    """dB below which a frame counts as silence: 30 dB under this clip's own speech level (floor -60 dBFS)."""
    return max(-60.0, float(np.percentile(db, 90)) - 30.0)


def active_mask(x: np.ndarray, sr: int) -> np.ndarray:
    """Per 20 ms frame: True where there is sound."""
    if len(x) == 0:
        return np.zeros(0, dtype=bool)
    db = frame_db(x, sr)
    return db >= silence_threshold(db)


def analyse(x: np.ndarray, sr: int) -> AudioStats:
    db = frame_db(x, sr)
    dur = len(x) / sr
    if len(x) == 0:
        return AudioStats(0.0, -120.0, -120.0, 0.0, 0.0, 0.0)
    speech_level = float(np.percentile(db, 90))
    noise_floor = float(np.percentile(db, 10))
    silent = db < silence_threshold(db)
    sil_runs = runs(silent)
    lead = sil_runs[0][1] * FRAME_S if sil_runs and sil_runs[0][0] == 0 else 0.0
    trail = (len(db) - sil_runs[-1][0]) * FRAME_S if sil_runs and sil_runs[-1][1] == len(db) else 0.0
    internal = [(a * FRAME_S, b * FRAME_S) for a, b in sil_runs if a > 0 and b < len(db) and (b - a) * FRAME_S >= 0.3]
    active = float((~silent).sum()) * FRAME_S
    # broadband noise: flat spectrum at speech-like loudness, sustained >= 0.3 s
    flat = frame_flatness(x, sr)
    noisy = (flat > 0.45) & (db > speech_level - 15.0)
    noise_runs = [(a * FRAME_S, b * FRAME_S) for a, b in runs(noisy) if (b - a) * FRAME_S >= 0.3]
    # clipping: |x| >= 0.98 for 3+ consecutive samples
    hot = np.abs(x) >= 0.98
    clipped = np.zeros_like(hot)
    if hot.any():
        k = hot[:-2] & hot[1:-1] & hot[2:]
        clipped[:-2] |= k
        clipped[1:-1] |= k
        clipped[2:] |= k
    clip_ratio = float(clipped.mean())
    clip_frames = frames(clipped.astype(np.float32), sr).max(axis=1) > 0
    clip_regions = [(a * FRAME_S, b * FRAME_S) for a, b in runs(clip_frames) if b - a >= 1]
    return AudioStats(
        dur, speech_level, noise_floor, active, lead, trail, internal, noise_runs, clip_ratio, clip_regions
    )


SENTENCE_PAUSE_S = 0.25  # a pause at least this long reads as a sentence or clause break
WORD_GAP_S = 0.08  # the shortest quiet stretch counted as a pause between words
LONG_CHUNK_MIN_S = 18.0  # long chunks (no pause found, and the second opinion) are 18-29 s


def pauses(x: np.ndarray, sr: int, min_s: float = WORD_GAP_S) -> list[tuple[float, float]]:
    """(centre, length) in seconds of every internal quiet stretch of at least ``min_s``."""
    if len(x) == 0:
        return []
    db = frame_db(x, sr)
    quiet = runs(db < silence_threshold(db))
    return [
        ((a + b) / 2 * FRAME_S, (b - a) * FRAME_S)
        for a, b in quiet
        if a > 0 and b < len(db) and (b - a) * FRAME_S >= min_s
    ]


def _quietest(x: np.ndarray, sr: int, lo: int, hi: int, avoid: list[int] | None = None, margin: int = 0) -> int:
    """Sample index of the quietest 100 ms in [lo, hi), keeping ``margin`` samples away from ``avoid`` points."""
    hop = int(sr * FRAME_S)
    smooth = np.convolve(frame_db(x, sr), np.ones(5) / 5, mode="same")  # 5 x 20 ms
    f_lo, f_hi = lo // hop, max(lo // hop + 1, min(len(smooth), hi // hop))
    window = smooth[f_lo:f_hi].copy()
    for p in avoid or []:
        a, b = max(f_lo, (p - margin) // hop), min(f_hi, (p + margin) // hop + 1)
        if b > a:
            window[a - f_lo : b - f_lo] = np.inf
    if not np.isfinite(window).any():  # everything excluded: ignore the avoid list
        window = smooth[f_lo:f_hi]
    return (f_lo + int(np.argmin(window))) * hop


def chunk_plan(x: np.ndarray, sr: int, target_s: float = 10.0, max_s: float = 29.0) -> list[tuple[int, int]]:
    """Cut points for ASR requests: at a pause near every ``target_s``, never longer than ``max_s``.

    Short chunks give the transcript more anchors for placing findings in the text (K5); Indic-Transcribe
    bills per second, so they cost the same. A chunk may stretch toward ``max_s`` where that keeps a sentence
    whole. In order of preference, the next cut is:
      1. a sentence pause within ``target_s`` +-40 % (6-14 s), the one closest to ``target_s``;
      2. the nearest sentence pause up to ``max_s`` (a long chunk beats cutting a sentence);
      3. a pause between words within 6-14 s;
      4. the quietest 100 ms between 18 s and ``max_s`` (unbroken sound); if the rest is at most ``max_s``,
         no cut: it is the last chunk.
    """
    n = len(x)
    lo_s, hi_s = 0.6 * target_s, 1.4 * target_s
    if n <= hi_s * sr:
        return [(0, n)]
    found = pauses(x, sr)

    def nearest(t0: float, lo: float, hi: float, min_len: float) -> float | None:
        cands = [c for c, length in found if t0 + lo <= c <= t0 + hi and length >= min_len]
        return min(cands, key=lambda c: abs(c - (t0 + target_s))) if cands else None

    out, pos = [], 0
    while n - pos > hi_s * sr:
        t0 = pos / sr
        cut_s = nearest(t0, lo_s, hi_s, SENTENCE_PAUSE_S)
        if cut_s is None:
            cut_s = nearest(t0, lo_s, max_s, SENTENCE_PAUSE_S)
        if cut_s is None:
            cut_s = nearest(t0, lo_s, hi_s, 0.0)
        if cut_s is None:
            if n - pos <= max_s * sr:  # no pause to cut at, and the rest fits in one request
                break
            cut = _quietest(x, sr, pos + int(LONG_CHUNK_MIN_S * sr), pos + int(max_s * sr))
        else:
            cut = int(cut_s * sr)
        out.append((pos, cut))
        pos = cut
    out.append((pos, n))
    return out


def alt_chunk_plan(
    x: np.ndarray, sr: int, primary: list[tuple[int, int]], max_s: float = 29.0
) -> list[tuple[int, int]]:
    """A different segmentation for the second-opinion transcription.

    Long audio: long chunks (18-29 s) cut at least 1.5 s away from every primary cut, so a word the first
    transcription lost at a chunk edge sits mid-chunk here. Audio the primary plan cut (14-29 s): one chunk.
    Short audio (one primary chunk): two chunks, cut at the quietest point in the middle 40 %.
    """
    n = len(x)
    if n > max_s * sr:
        avoid = [b for _, b in primary[:-1]]
        out, pos = [], 0
        while n - pos > max_s * sr:
            cut = _quietest(x, sr, pos + int(LONG_CHUNK_MIN_S * sr), pos + int(max_s * sr), avoid, int(1.5 * sr))
            out.append((pos, cut))
            pos = cut
        out.append((pos, n))
        return out
    if len(primary) > 1 or n < 3 * sr:
        return [(0, n)]
    cut = _quietest(x, sr, int(0.3 * n), int(0.7 * n))
    return [(0, cut), (cut, n)]


def silence(seconds: float, sr: int) -> np.ndarray:
    return np.zeros(int(seconds * sr), dtype=np.float32)


def quiet_points(x: np.ndarray, sr: int, min_gap_s: float = 0.12) -> list[float]:
    """Centres (s) of short pauses, used by the injector to cut at word boundaries."""
    return [c for c, _ in pauses(x, sr, min_gap_s)]
