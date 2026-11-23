"""ffmpeg / ffprobe plumbing and 16 kHz PCM access.

Only two things are delegated to ffmpeg: probing a container and decoding it to
16 kHz mono PCM. Everything after that — cutting, slicing, energy analysis —
happens on the numpy array, which is exact (sample-addressed) and much faster
than shelling out per chunk.

Binary discovery tries ``FFMPEG_BIN`` / ``FFPROBE_BIN``, then ``PATH``, then the
usual package-manager locations. That last step matters because a bare
``/opt/homebrew/bin`` install is not always on ``PATH`` inside an IDE.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .errors import MediaError

TARGET_SAMPLE_RATE = 16_000
# Indic-Speak returns 24 kHz PCM16 mono. The dubbed track stays at that rate so
# speech is not needlessly downsampled; only the ASR input is 16 kHz.
TTS_SAMPLE_RATE = 24_000

_FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")
_BIN_NAMES = {"ffmpeg": "FFMPEG_BIN", "ffprobe": "FFPROBE_BIN"}


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration_s: float
    has_video: bool
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "duration_s": round(self.duration_s, 3),
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "width": self.width,
            "height": self.height,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


def find_binary(name: str) -> str:
    override = os.environ.get(_BIN_NAMES[name], "").strip()
    if override:
        if Path(override).exists() or shutil.which(override):
            return override
        raise MediaError(f"{_BIN_NAMES[name]} is set to {override!r} but that file does not exist")
    found = shutil.which(name)
    if found:
        return found
    for directory in _FALLBACK_DIRS:
        candidate = Path(directory) / name
        if candidate.exists():
            return str(candidate)
    raise MediaError(
        f"{name} was not found. Install ffmpeg (macOS: `brew install ffmpeg`, "
        f"Debian/Ubuntu: `sudo apt-get install -y ffmpeg`) or point "
        f"{_BIN_NAMES[name]} at the binary."
    )


def ensure_tools() -> None:
    find_binary("ffmpeg")
    find_binary("ffprobe")


def _run(argv: list[str], *, what: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise MediaError(f"{what} failed: {argv[0]} is not installed") from exc
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-6:]
        detail = " | ".join(tail) if tail else "no diagnostic output"
        raise MediaError(f"{what} failed (exit {exc.returncode}): {detail}") from exc


def probe(path: Path | str) -> MediaInfo:
    """Read container and stream facts without decoding the whole file."""
    src = Path(path)
    if not src.exists():
        raise MediaError(f"input file does not exist: {src}")
    if src.stat().st_size == 0:
        raise MediaError(f"input file is empty: {src}")

    result = _run(
        [
            find_binary("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(src),
        ],
        what=f"ffprobe on {src.name}",
    )
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise MediaError(f"ffprobe returned unreadable JSON for {src.name}") from exc

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    for candidate in (
        payload.get("format", {}).get("duration"),
        (audio or {}).get("duration"),
        (video or {}).get("duration"),
    ):
        try:
            if candidate is not None and float(candidate) > 0:
                duration = float(candidate)
                break
        except (TypeError, ValueError):
            continue

    if duration <= 0:
        raise MediaError(
            f"could not determine the duration of {src.name}; the file may be truncated "
            "or use an unsupported container"
        )

    return MediaInfo(
        path=str(src),
        duration_s=duration,
        has_video=video is not None,
        has_audio=audio is not None,
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        width=(video or {}).get("width"),
        height=(video or {}).get("height"),
        sample_rate=_as_int((audio or {}).get("sample_rate")),
        channels=_as_int((audio or {}).get("channels")),
    )


def extract_pcm_wav(
    src: Path | str,
    dst: Path | str,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> None:
    """Decode any container to mono signed-16 PCM WAV at the given rate."""
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            find_binary("ffmpeg"),
            "-nostdin",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(out),
        ],
        what=f"audio extraction from {Path(src).name}",
    )
    if not out.exists() or out.stat().st_size == 0:
        raise MediaError(f"audio extraction produced no output for {Path(src).name}")


def read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    """Return float32 mono samples in [-1, 1] plus the sample rate."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise MediaError(f"expected 16-bit PCM in {path}, found {width * 8}-bit")
    data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def to_wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def write_wav(path: Path | str, samples: np.ndarray, rate: int) -> None:
    Path(path).write_bytes(to_wav_bytes(samples, rate))


def slice_samples(samples: np.ndarray, rate: int, start_s: float, end_s: float) -> np.ndarray:
    """Sample-exact slice, clamped to the buffer."""
    start = max(0, min(len(samples), int(round(start_s * rate))))
    end = max(start, min(len(samples), int(round(end_s * rate))))
    return samples[start:end]


def file_digest(path: Path | str, *, chunk_size: int = 1 << 20) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def read_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Decode an in-memory WAV the same way ``read_wav`` decodes a file."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise MediaError(f"expected 16-bit PCM audio, found {width * 8}-bit")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def wav_duration_bytes(data: bytes) -> float:
    samples, rate = read_wav_bytes(data)
    return len(samples) / float(rate)


def concat_wav_bytes(parts: list[bytes]) -> bytes:
    """Join WAV clips that share a sample rate and channel layout."""
    if not parts:
        raise MediaError("cannot concatenate zero audio parts")
    if len(parts) == 1:
        return parts[0]
    decoded = [read_wav_bytes(part) for part in parts]
    rate = decoded[0][1]
    for _, other_rate in decoded[1:]:
        if other_rate != rate:
            raise MediaError("cannot concatenate audio with mismatched sample rates")
    joined = np.concatenate([samples for samples, _ in decoded])
    return to_wav_bytes(joined, rate)


def time_stretch_wav(data: bytes, tempo: float) -> bytes:
    """Change speaking rate without changing pitch, using ffmpeg's atempo.

    ``tempo`` is a speed factor: 1.0 is unchanged, 1.2 is 20% faster. Values
    near 1.0 skip the process entirely so the audio is bit-identical.
    """
    if abs(tempo - 1.0) < 0.005:
        return data
    if not (0.5 <= tempo <= 2.0):
        raise MediaError(f"atempo only supports 0.5-2.0 per stage, got {tempo}")
    try:
        result = subprocess.run(
            [
                find_binary("ffmpeg"),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-filter:a",
                f"atempo={tempo:.4f}",
                "-ac",
                "1",
                "-ar",
                str(TTS_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
                "pipe:1",
            ],
            input=data,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-300:]
        raise MediaError(f"time stretching failed: {tail}") from exc
    if not result.stdout:
        raise MediaError("time stretching produced no audio")
    return result.stdout


def trim_edges(
    samples: np.ndarray,
    sample_rate: int,
    *,
    threshold_db: float = -45.0,
    pad_s: float = 0.02,
    min_keep_s: float = 0.05,
) -> np.ndarray:
    """Strip leading and trailing silence so a clip starts when it should.

    TTS output almost always opens with a little silence. Left in place it shifts
    every clip late by 100-300 ms, which is the entire sync budget. Trimming
    restores a short pad so the onset is not unnaturally abrupt.
    """
    if samples.size == 0:
        return samples
    threshold = 10.0 ** (threshold_db / 20.0)
    above = np.abs(samples) > threshold
    if not above.any():
        return samples
    first = int(np.argmax(above))
    last = len(above) - int(np.argmax(above[::-1]))
    pad = int(pad_s * sample_rate)
    first = max(0, first - pad)
    last = min(len(samples), last + pad)
    if (last - first) / sample_rate < min_keep_s:
        return samples
    return samples[first:last]


def trimmed_seconds(original: int, trimmed: int, sample_rate: int) -> float:
    return max(0, original - trimmed) / float(sample_rate)


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
