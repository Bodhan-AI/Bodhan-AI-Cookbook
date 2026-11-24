"""Join PCM16 WAV chunks with silences, using only the standard library."""

from __future__ import annotations

import io
import wave


class WavError(ValueError):
    pass


def read_wav(data: bytes) -> tuple[bytes, int]:
    """(PCM frames, sample rate) of a mono PCM16 WAV."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise WavError("expected mono 16-bit PCM")
            return w.readframes(w.getnframes()), w.getframerate()
    except (wave.Error, EOFError) as exc:
        raise WavError(str(exc)) from exc


def duration_s(data: bytes) -> float:
    frames, rate = read_wav(data)
    return len(frames) / 2 / rate


def silence(seconds: float, rate: int) -> bytes:
    return b"\x00\x00" * round(seconds * rate)


def join(parts: list[bytes], rate: int) -> bytes:
    """A WAV file from PCM frames."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(parts))
    return buffer.getvalue()
