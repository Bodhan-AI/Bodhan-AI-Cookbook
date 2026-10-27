"""File validation and conversion with ffmpeg.

Everything Bodhan receives is 16 kHz mono 16-bit WAV: the Speech-to-Text API
refuses M4A, AAC, WebM and AMR, and WAV is its documented format.
"""

from __future__ import annotations

import io
import json
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16_000


class AudioError(Exception):
    """A problem with the uploaded file. ``code`` is returned to the app."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def sniff_format(head: bytes) -> str | None:
    """Identify the container from the first bytes, so a renamed file is caught."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:5] == b"#!AMR":
        return "amr"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[4:8] == b"ftyp":
        return "mp4"  # .m4a, and .aac files saved in an MP4 container
    if head[:4] == b"ADIF":
        return "aac"
    if head[:3] == b"ID3":
        return "mp3"  # ID3 tags also precede some AAC/FLAC files; ffprobe settles it
    if len(head) >= 2 and head[0] == 0xFF:
        if head[1] & 0xF6 == 0xF0:
            return "aac"  # ADTS
        if head[1] & 0xE0 == 0xE0 and head[1] & 0x06:
            return "mp3"  # MPEG audio frame sync, layer bits set
    return None


def validate_upload(path: Path, filename: str, *, allowed_extensions: tuple[str, ...], max_bytes: int) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in allowed_extensions:
        raise AudioError(
            "unsupported_format",
            f"'{ext or filename}' files are not supported. Use one of: {', '.join(allowed_extensions)}.",
            415,
        )
    size = path.stat().st_size
    if size == 0:
        raise AudioError("empty_file", "The file is empty.")
    if size > max_bytes:
        raise AudioError(
            "file_too_large", f"The file is {size / 1e6:.1f} MB; the limit is {max_bytes // 1_000_000} MB.", 413
        )
    with path.open("rb") as fh:
        head = fh.read(16)
    if sniff_format(head) is None:
        raise AudioError("unsupported_format", "The file content is not a recognised audio format.", 415)


@dataclass(frozen=True)
class ProbeResult:
    duration: float | None  # None when the container does not record it (e.g. browser WebM)


def probe(path: Path, ffprobe: str = "ffprobe") -> ProbeResult:
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AudioError("server_misconfigured", "ffprobe is not installed on the server.", 500) from exc
    if proc.returncode != 0:
        raise AudioError("invalid_audio", "The audio file could not be read. It may be damaged.")
    info = json.loads(proc.stdout or b"{}")
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise AudioError("invalid_audio", "The file has no audio track.")
    raw = info.get("format", {}).get("duration")
    try:
        duration = float(raw) if raw not in (None, "N/A") else None
    except ValueError:
        duration = None
    return ProbeResult(duration=duration)


def check_duration(duration: float, max_seconds: int) -> None:
    if duration <= 0.1:
        raise AudioError("invalid_audio", "The recording is empty or too short.")
    if duration > max_seconds:
        raise AudioError(
            "call_too_long", f"The call is {duration / 60:.1f} minutes long; the limit is {max_seconds // 60} minutes."
        )


def convert_to_wav(src: Path, dst: Path, *, max_seconds: int, ffmpeg: str = "ffmpeg") -> None:
    """Decode any supported format to 16 kHz mono PCM WAV.

    Decoding stops one second past the limit so an over-long file is detected
    without decoding all of it.
    """
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16",
        "-t", str(max_seconds + 1), "-f", "wav", str(dst),
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    except FileNotFoundError as exc:
        raise AudioError("server_misconfigured", "ffmpeg is not installed on the server.", 500) from exc
    if proc.returncode != 0 or not dst.exists():
        raise AudioError("invalid_audio", "The audio could not be converted. The file may be damaged.")


def read_wav(path: Path) -> np.ndarray:
    """Return 16-bit mono samples from a WAV written by ``convert_to_wav``."""
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != SAMPLE_RATE:
            raise AudioError("invalid_audio", "Unexpected WAV layout after conversion.", 500)
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def wav_bytes(samples: np.ndarray, start: float, end: float) -> bytes:
    """Encode ``samples[start:end]`` (seconds) as an in-memory WAV file."""
    lo = max(0, int(round(start * SAMPLE_RATE)))
    hi = min(len(samples), int(round(end * SAMPLE_RATE)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(samples[lo:hi].tobytes())
    return buf.getvalue()
