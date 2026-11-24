"""PCM WAV signal measurements, without learned audio models."""

import array
import sys
import wave


def analyze_wav(path):
    with wave.open(str(path), "rb") as audio:
        rate, channels, width = audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
        if width != 2 or audio.getcomptype() != "NONE":
            raise ValueError("Signal checks currently support uncompressed 16-bit PCM WAV only")
        samples = array.array("h", audio.readframes(audio.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    duration = len(samples) / channels / rate
    clipped = sum(abs(s) >= 32760 for s in samples) / max(1, len(samples))
    frame = max(1, rate // 50) * channels
    quiet = []
    for offset in range(0, len(samples), frame):
        chunk = samples[offset : offset + frame]
        rms = (sum(s * s for s in chunk) / max(1, len(chunk))) ** 0.5
        if rms < 100:
            quiet.append([offset / channels / rate, min(duration, (offset + len(chunk)) / channels / rate)])
    intervals = []
    for start, end in quiet:
        if intervals and abs(intervals[-1][1] - start) < 1e-6:
            intervals[-1][1] = end
        else:
            intervals.append([start, end])
    return {
        "duration_seconds": duration,
        "peak_amplitude": max((abs(s) for s in samples), default=0),
        "clipped_sample_fraction": clipped,
        "silence_intervals_seconds": intervals,
        "note": "Signal measurements only; silence and peaks require context before flagging garbled speech.",
    }
