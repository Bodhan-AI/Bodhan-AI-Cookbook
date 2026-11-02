"""
Find what to transcribe: every stretch of the audio with sound in it, so nothing audible is skipped.

Two signals, one 32 ms window (512 samples at 16 kHz) at a time:
- Loudness decides WHAT is sent. Anything within SOUND_RANGE_DB of the track's loud parts is sound:
  speech, singing, speech over music. Only real quiet is skipped (it is billed too).
- The Silero voice activity detector (silero-vad-lite, MIT) says WHERE people speak. It ignores music
  and often singing, so it is never used to drop audio: only to choose cut points and place lines.
"""

import array
import math
import operator
import wave
from dataclasses import dataclass

# Speech starts when a window's speech probability reaches START_PROBABILITY and continues while it
# stays at END_PROBABILITY or above
START_PROBABILITY = 0.5
END_PROBABILITY = 0.35
# Sound: windows within this many dB of the track's loud parts (its 95th percentile loudness)...
SOUND_RANGE_DB = 35.0
# ...but never quieter than QUIETEST_SOUND_DB (hiss in a quiet track) or louder than LOUDEST_THRESHOLD_DB
QUIETEST_SOUND_DB = -60.0
LOUDEST_THRESHOLD_DB = -35.0
# Quiet shorter than this (seconds) is a breath or a beat, not a pause
MIN_PAUSE = 0.35
# Sound shorter than this is a click or blip
MIN_SOUND = 0.25
# Added around each stretch so soft word beginnings and endings aren't clipped
PADDING = 0.15
# A piece's lines are placed on its detected speech when that covers at least this share of its sound
# (speech over music); otherwise (singing, speech Silero missed) on the sound itself
SPEECH_SHARE_FOR_TIMING = 0.5

Stretch = tuple[float, float]


@dataclass
class Analysis:
    step: float  # seconds per window
    total: float  # seconds of audio
    probabilities: list[float]  # speech probability per window
    loudness: list[float]  # dBFS per window
    speech: list[Stretch]  # where Silero hears speech
    sound: list[Stretch]  # everything to transcribe: all sound, speech included

    def quietest_point(self, start: float, end: float) -> float:
        """The best place to cut between start and end: outside speech if possible, then the quietest window."""
        first, last = int(start / self.step), min(len(self.loudness), math.ceil(end / self.step))
        if first >= last:
            return end
        best = min(range(first, last), key=lambda i: (self.probabilities[i] >= END_PROBABILITY, self.loudness[i]))
        return min(end, max(start, (best + 0.5) * self.step))

    def timing_stretches(self, start: float, end: float, sound: list[Stretch]) -> list[Stretch]:
        """The stretches to place one piece's lines on: its speech if Silero found enough of it, else its sound."""
        speech = [(max(s, start), min(e, end)) for s, e in self.speech if e > start and s < end]
        if sum(e - s for s, e in speech) >= SPEECH_SHARE_FOR_TIMING * sum(e - s for s, e in sound):
            return speech
        return sound


def analyse(wav_path: str) -> Analysis:
    """Analyse a 16 kHz mono 16-bit WAV."""
    from silero_vad_lite import SileroVAD  # imported here so tests of the logic don't load the model

    with wave.open(wav_path, "rb") as wav:
        rate, frames = wav.getframerate(), wav.readframes(wav.getnframes())
    vad = SileroVAD(rate)
    size = vad.window_size_samples
    pcm = array.array("h", frames)
    windows = [pcm[i : i + size] for i in range(0, len(pcm) - size + 1, size)]
    probabilities = [vad.process(array.array("f", (s / 32768.0 for s in w))) for w in windows]
    loudness = [_decibels(w) for w in windows]
    return analysis_from(probabilities, loudness, size / rate, len(pcm) / rate)


def analysis_from(probabilities: list[float], loudness: list[float], step: float, total: float) -> Analysis:
    """The stretches of speech and of sound, from per-window probabilities and loudness."""
    speech = stretches(speech_flags(probabilities), step, total)
    threshold = sound_threshold(loudness)
    sound = stretches([db >= threshold for db in loudness], step, total)
    # Silero can hear quiet speech that loudness misses: send that too
    return Analysis(step, total, probabilities, loudness, speech, merge(sound + speech))


def speech_flags(probabilities: list[float]) -> list[bool]:
    """Per window: is someone speaking? Starts at START_PROBABILITY, continues down to END_PROBABILITY."""
    flags, speaking = [], False
    for p in probabilities:
        speaking = p >= (END_PROBABILITY if speaking else START_PROBABILITY)
        flags.append(speaking)
    return flags


def sound_threshold(loudness: list[float]) -> float:
    """Loudness (dBFS) at and above which a window counts as sound, relative to the track's loud parts."""
    if not loudness:
        return LOUDEST_THRESHOLD_DB
    loud = sorted(loudness)[int(0.95 * (len(loudness) - 1))]
    return min(LOUDEST_THRESHOLD_DB, max(QUIETEST_SOUND_DB, loud - SOUND_RANGE_DB))


def stretches(flags: list[bool], step: float, total: float) -> list[Stretch]:
    """Turn per-window flags into stretches: bridge gaps under MIN_PAUSE, drop blips under MIN_SOUND, pad."""
    found: list[Stretch] = []
    start = None
    for i, on in enumerate(flags + [False]):
        t = i * step
        if on and start is None:
            start = t
        elif not on and start is not None:
            if found and start - found[-1][1] < MIN_PAUSE:
                found[-1] = (found[-1][0], t)
            else:
                found.append((start, t))
            start = None
    padded = [(max(0.0, s - PADDING), min(total, e + PADDING)) for s, e in found if e - s >= MIN_SOUND]
    return merge(padded)


def merge(items: list[Stretch]) -> list[Stretch]:
    """Sort and join overlapping stretches."""
    merged: list[Stretch] = []
    for s, e in sorted(items):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _decibels(window) -> float:
    power = sum(map(operator.mul, window, window)) / len(window)
    return 10 * math.log10(power / (32768.0 * 32768.0)) if power > 0 else -120.0
