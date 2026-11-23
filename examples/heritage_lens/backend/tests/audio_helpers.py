import math
import struct
import wave
from pathlib import Path


def write_tone_wav(path: Path, seconds: float, *, rate=16000, freq=440.0, gaps=(), amplitude=12000, channels=1):
    """Write a sine tone; `gaps` is a list of (start_s, end_s) ranges of digital silence."""
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        t = i / rate
        silent = any(a <= t < b for a, b in gaps)
        v = 0 if silent else int(amplitude * math.sin(2 * math.pi * freq * t))
        frames += struct.pack("<h", v) * channels
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def make_wav_bytes(seconds: float, *, rate=24000, freq=300.0) -> bytes:
    import io
    buf = io.BytesIO()
    n = int(seconds * rate)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n)))
    return buf.getvalue()
