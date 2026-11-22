from __future__ import annotations

import math
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
from mutagen import File as MutagenFile
from pydub import AudioSegment

AUDIO_EXTENSIONS = {".wav", ".flac", ".ogg", ".mp3"}
MAX_TRANSCRIPTION_SECONDS = 30.0
SAFETY_CHUNK_SECONDS = 25.0
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()


class AudioProcessingError(ValueError):
    """Raised when a local audio file cannot be validated or split."""


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    duration_seconds: float
    start_seconds: float
    end_seconds: float


def audio_duration_seconds(path: str | Path) -> float:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_file():
        raise AudioProcessingError(f"Audio file does not exist: {resolved}")
    if resolved.stat().st_size == 0:
        raise AudioProcessingError("Audio file is empty")
    if resolved.suffix.lower() not in AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(AUDIO_EXTENSIONS))
        raise AudioProcessingError(
            f"Unsupported audio file extension; supported extensions: {allowed}"
        )

    try:
        media = MutagenFile(resolved)
        duration = float(media.info.length) if media is not None and media.info is not None else None
    except Exception as exc:
        raise AudioProcessingError("Audio file is corrupt or unreadable") from exc

    if duration is None or not math.isfinite(duration) or duration <= 0:
        raise AudioProcessingError("Could not determine a valid positive audio duration")
    return duration


def chunk_ranges(duration_seconds: float) -> list[tuple[float, float]]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise AudioProcessingError("Could not determine a valid positive audio duration")
    if duration_seconds <= MAX_TRANSCRIPTION_SECONDS:
        return [(0.0, duration_seconds)]

    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds:
        end = min(start + SAFETY_CHUNK_SECONDS, duration_seconds)
        ranges.append((start, end))
        start = end
    return ranges


def chunk_count(duration_seconds: float) -> int:
    return len(chunk_ranges(duration_seconds))


@contextmanager
def chunk_audio_file(
    path: str | Path,
    *,
    temp_root: str | Path | None = None,
) -> Iterator[list[AudioChunk]]:
    resolved = Path(path).expanduser().resolve(strict=True)
    duration = audio_duration_seconds(resolved)
    ranges = chunk_ranges(duration)

    if len(ranges) == 1:
        yield [AudioChunk(resolved, duration, 0.0, duration)]
        return

    temp_root_path = Path(temp_root).resolve() if temp_root is not None else None
    if temp_root_path is not None:
        temp_root_path.mkdir(parents=True, exist_ok=True)

    try:
        audio = AudioSegment.from_file(
            str(resolved),
            format=resolved.suffix.lower().lstrip("."),
        )
    except Exception as exc:
        raise AudioProcessingError(
            "Audio file is corrupt or could not be decoded locally"
        ) from exc

    if len(audio) <= 0:
        raise AudioProcessingError("Audio file contains no decodable samples")

    decoded_duration = float(len(audio) / 1000.0)
    if not math.isfinite(decoded_duration) or decoded_duration <= 0:
        raise AudioProcessingError("Decoded audio has no valid duration")
    if abs(decoded_duration - duration) > 1.0:
        raise AudioProcessingError(
            "Local audio duration could not be matched reliably"
        )

    chunks: list[AudioChunk] = []
    with tempfile.TemporaryDirectory(
        prefix="bodhan-transcribe-",
        dir=temp_root_path,
    ) as temp_dir:
        temp_path = Path(temp_dir)
        try:
            for index, (start, end) in enumerate(ranges):
                start_ms = int(round(start * 1000))
                end_ms = int(round(end * 1000))
                segment = audio[start_ms:end_ms]
                if len(segment) <= 0:
                    raise AudioProcessingError(
                        f"Audio chunk {index + 1} contains no samples"
                    )

                chunk_path = temp_path / f"chunk-{index + 1:04d}.wav"
                segment.export(str(chunk_path), format="wav")
                actual_duration = float(len(segment) / 1000.0)
                chunks.append(
                    AudioChunk(
                        chunk_path,
                        actual_duration,
                        float(start),
                        float(end),
                    )
                )
            yield chunks
        finally:
            chunks.clear()
