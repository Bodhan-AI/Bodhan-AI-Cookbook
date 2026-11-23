"""WAV parsing, building and concatenation (stdlib only)."""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Sequence


class WavFormatError(ValueError):
    pass


@dataclass(frozen=True)
class WavFormat:
    channels: int
    sample_width: int  # bytes per sample
    frame_rate: int

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * self.sample_width


def parse_wav(data: bytes) -> tuple[WavFormat, bytes]:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavFormatError("Data is not a RIFF/WAVE file.")
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            fmt = WavFormat(w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise WavFormatError(f"Invalid WAV data: {exc}") from exc
    return fmt, frames


def build_wav(fmt: WavFormat, frames: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(fmt.channels)
        w.setsampwidth(fmt.sample_width)
        w.setframerate(fmt.frame_rate)
        w.writeframes(frames)
    return buf.getvalue()


def wav_duration_seconds(data: bytes) -> float:
    fmt, frames = parse_wav(data)
    return len(frames) / fmt.bytes_per_frame / fmt.frame_rate


def silence(fmt: WavFormat, milliseconds: int) -> bytes:
    n_frames = int(fmt.frame_rate * milliseconds / 1000)
    return b"\x00" * (n_frames * fmt.bytes_per_frame)


def concat_wavs(wavs: Sequence[bytes], gap_ms: int = 0) -> bytes:
    """Join WAV files in order. All must share channels/width/rate; mismatches raise."""
    if not wavs:
        raise WavFormatError("No audio segments to join.")
    fmt0: WavFormat | None = None
    parts: list[bytes] = []
    for i, data in enumerate(wavs):
        fmt, frames = parse_wav(data)
        if fmt0 is None:
            fmt0 = fmt
        elif fmt != fmt0:
            raise WavFormatError(
                f"Segment {i} has format {fmt} but segment 0 has {fmt0}; refusing to join incompatible audio."
            )
        if i > 0 and gap_ms > 0:
            parts.append(silence(fmt0, gap_ms))
        parts.append(frames)
    assert fmt0 is not None
    return build_wav(fmt0, b"".join(parts))
