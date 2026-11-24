"""Speech checks.

Two layers, deliberately separated by cost:

* **Signal-level, free** — duration, silence fraction and clipping are read
  straight from the WAV header and samples. No model call, so they run on every
  item that has audio.
* **Content-level, paid** — back-transcription with Indic-Transcribe, then a
  character-similarity comparison against the expected text. Only run on a
  sample, or when the free checks already look suspicious.

That split is the "cheap checks first, costly checks where they pay off" idea
the brief asks for.
"""

from __future__ import annotations

import io
import re
import wave
from dataclasses import dataclass

import numpy as np

from ..checks.reference import chrf
from ..normalize import normalize
from ..spans import span_preserving
from .base import Flag, ItemContext


@dataclass
class AudioStats:
    duration_s: float
    silence_fraction: float
    clipping_fraction: float
    rms: float
    sample_rate: int


def analyse_wav(data: bytes) -> AudioStats | None:
    """Read duration, silence and clipping from a PCM WAV. None if unreadable."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getnchannels() not in (1, 2):
                return None
            sr = w.getframerate()
            n = w.getnframes()
            raw = w.readframes(n)
            width = w.getsampwidth()
    except Exception:  # noqa: BLE001 - malformed audio is an item-level problem
        return None

    if width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        return None

    if samples.size == 0:
        return None

    duration = n / float(sr) if sr else 0.0
    frame = max(1, int(sr * 0.02))  # 20 ms frames
    trimmed = samples[: (samples.size // frame) * frame]
    if trimmed.size:
        frames = trimmed.reshape(-1, frame)
        rms_frames = np.sqrt(np.mean(frames ** 2, axis=1))
        silence = float(np.mean(rms_frames < 0.01))
    else:
        silence = 0.0
    clipping = float(np.mean(np.abs(samples) > 0.985))
    rms = float(np.sqrt(np.mean(samples ** 2)))

    return AudioStats(duration, silence, clipping, rms, sr)


def check_audio_signal(ctx: ItemContext, thresholds) -> list[Flag]:
    """Free checks on the audio itself."""
    if not ctx.audio:
        return []
    try:
        with open(ctx.audio, "rb") as fh:
            data = fh.read()
    except OSError:
        return [Flag(category="speech_garbled", severity="major", span=None,
                     evidence=f"audio file missing or unreadable: {ctx.audio}",
                     check="speech.audio_missing", confidence=1.0)]

    stats = analyse_wav(data)
    if stats is None:
        return [Flag(category="speech_garbled", severity="minor", span=None,
                     evidence="audio could not be decoded as PCM WAV (non-WAV formats "
                              "need ffmpeg conversion first)",
                     check="speech.undecodable", confidence=0.5)]

    flags: list[Flag] = []
    if stats.duration_s < 0.4:
        flags.append(Flag(category="speech_omission", severity="critical", span=None,
                          evidence=f"audio is only {stats.duration_s:.2f}s long",
                          check="speech.too_short", confidence=0.9))
    if stats.silence_fraction > thresholds.silence_max_fraction:
        flags.append(Flag(category="speech_garbled", severity="major", span=None,
                          evidence=f"{stats.silence_fraction:.0%} of frames are silent",
                          check="speech.silence", confidence=0.8))
    if stats.clipping_fraction > thresholds.clipping_max_fraction:
        flags.append(Flag(category="speech_garbled", severity="minor", span=None,
                          evidence=f"{stats.clipping_fraction:.1%} of samples are clipped",
                          check="speech.clipping", confidence=0.7))
    if stats.rms < 0.005:
        flags.append(Flag(category="speech_garbled", severity="major", span=None,
                          evidence=f"audio is near-silent (RMS {stats.rms:.4f})",
                          check="speech.near_silent", confidence=0.85))
    return flags


def _repeated_phrase(text: str, n: int = 3) -> tuple[int, int, str] | None:
    r"""Find a phrase repeated back-to-back, using whitespace tokens.

    Deliberately avoids \b and \w: Python treats Indic combining marks (Mn) as
    non-word characters, so "है" is not one token and a backreference across it
    never matches. Whitespace splitting is script-agnostic and correct.
    """
    words = text.split()
    if len(words) < n * 2:
        return None
    offsets, pos = [], 0
    for w in words:
        start = text.find(w, pos)
        offsets.append((start, start + len(w)))
        pos = start + len(w)
    for i in range(len(words) - n + 1):
        a = [w.strip(".,!?;:।\"'") for w in words[i:i + n]]
        if i + 2 * n > len(words):
            break
        b = [w.strip(".,!?;:।\"'") for w in words[i + n:i + 2 * n]]
        if a == b and a:
            return offsets[i][0], offsets[i + 2 * n - 1][1], " ".join(words[i:i + n])
    return None


def check_transcript(ctx: ItemContext, transcript: str, expected: str,
                     thresholds) -> list[Flag]:
    """Content checks on a back-transcription (already paid for)."""
    flags: list[Flag] = []
    if not transcript:
        return [Flag(category="speech_omission", severity="critical", span=None,
                     evidence="back-transcription is empty — nothing intelligible was spoken",
                     check="speech.empty_transcript", confidence=0.9)]

    # Repetition / looping.
    rep = _repeated_phrase(transcript)
    if rep:
        start, end, phrase = rep
        flags.append(Flag(category="speech_repetition", severity="major",
                          span=(start, end),
                          evidence=f"repeated phrase in transcript: '{phrase[:60]}'",
                          check="speech.repetition", confidence=0.8))

    # Omission / truncation against the expected text.
    if expected:
        score = chrf(transcript, expected)
        if score < thresholds.speech_similarity_min:
            severity = "critical" if score < 0.45 else "major"
            flags.append(Flag(
                category="speech_omission", severity=severity,
                span=(0, min(len(transcript), 60)),
                evidence=f"back-transcription similarity to expected text = {score:.3f} "
                         f"(threshold {thresholds.speech_similarity_min}); "
                         f"heard: '{transcript[:80]}'",
                check="speech.similarity", confidence=0.7,
            ))

        # A truncated ending is the most common synthesis failure.
        if expected and transcript and not normalize(transcript).endswith(
                normalize(expected)[-20:].strip()):
            flags.append(Flag(category="speech_omission", severity="minor", span=None,
                              evidence="transcript does not end where the expected text ends "
                                       "(possible truncation)",
                              check="speech.truncation", confidence=0.45))
    return flags
