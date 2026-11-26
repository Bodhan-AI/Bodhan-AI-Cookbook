"""Does a "separate" output leak into what the subtitles listen to?

Dubbing must play where the subtitle capture (Windows' default output, via loopback) can't hear
it, or the app transcribes its own voice. Some outputs look separate but are mixed back into the
speakers, e.g. ASUS's "AI Noise-cancelling Output".

The check plays a short speech clip on the device and measures how much of that exact clip comes
back in the loopback (a matched filter, so unrelated meeting audio barely registers).

It must be speech: noise-cancelling outputs remove steady sounds. Measured 2026-11-26 through
the ASUS output: a test tone came back at gain 0.01, the speech clip at 0.88 (0.84 when played
directly on the speakers).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.audio.wavutil import decode_wav

CHECK_SOUND = Path(__file__).resolve().parent.parent / "assets" / "check_speech.wav"  # 1.2 s, 16 kHz
LEAK_GAIN = 0.2  # more of the clip than this coming back = the device leaks


def check_sound() -> np.ndarray:
    audio, rate = decode_wav(CHECK_SOUND.read_bytes())
    assert rate == 16000, rate
    return audio


def leak_gain(captured: np.ndarray, ref: np.ndarray) -> float:
    """How strongly `ref` appears anywhere in `captured`: its best least-squares gain over all
    time offsets (1.0 = came back at full level, 0.0 = absent)."""
    energy = float(np.dot(ref, ref))
    if energy == 0 or len(captured) < len(ref):
        return 0.0
    n = 1 << int(np.ceil(np.log2(len(captured) + len(ref))))
    corr = np.fft.irfft(np.fft.rfft(captured, n) * np.conj(np.fft.rfft(ref, n)), n)
    return float(np.abs(corr[: len(captured) - len(ref) + 1]).max() / energy)
