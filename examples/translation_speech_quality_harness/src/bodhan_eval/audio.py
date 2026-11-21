from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

import numpy as np

from .config import CHECK_THRESHOLDS


class FfmpegMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class WavData:
    samples: np.ndarray
    sr: int

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sr


@dataclass(frozen=True)
class Chunk:
    start_s: float
    end_s: float
    samples: np.ndarray


def to_wav(path_or_bytes: Path | str | bytes, ffmpeg: bool | None = None) -> WavData:
    if isinstance(path_or_bytes, bytes):
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            raise FfmpegMissing("ffmpeg is required to convert audio")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.audio"
            target = Path(directory) / "output.wav"
            source.write_bytes(path_or_bytes)
            subprocess.run(
                [ffmpeg_path, "-y", "-i", str(source), "-ac", "1", "-ar", "24000", str(target)],
                check=True,
                capture_output=True,
            )
            return to_wav(target)

    path = Path(path_or_bytes)
    suffix = path.suffix.lower()
    if suffix not in {".wav"}:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            raise FfmpegMissing("ffmpeg is required to convert audio")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.wav"
            subprocess.run(
                [ffmpeg_path, "-y", "-i", str(path), "-ac", "1", "-ar", "24000", str(target)],
                check=True,
                capture_output=True,
            )
            return to_wav(target)

    with wave.open(str(path), "rb") as handle:
        sr = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
        channels = handle.getnchannels()
        width = handle.getsampwidth()
    if width == 2:
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    else:
        samples = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
        samples = (samples - 128.0) / 128.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return WavData(samples=samples, sr=sr)


def voiced_regions(
    wav: WavData,
    threshold: float = CHECK_THRESHOLDS["voiced_peak"]["value"],
    frame_ms: int = 20,
) -> list[tuple[float, float]]:
    frame = max(1, int(wav.sr * frame_ms / 1000))
    count = len(wav.samples) // frame
    regions: list[tuple[float, float]] = []
    start: int | None = None
    for index in range(count):
        frame_values = wav.samples[index * frame:(index + 1) * frame]
        voiced = bool(np.max(np.abs(frame_values)) >= threshold)
        if voiced and start is None:
            start = index
        if not voiced and start is not None:
            end = index
            regions.append((start * frame_ms / 1000, end * frame_ms / 1000))
            start = None
    if start is not None:
        regions.append((start * frame_ms / 1000, count * frame_ms / 1000))
    return regions


def silence_stats(wav: WavData) -> dict[str, float | list[tuple[float, float]]]:
    regions = voiced_regions(wav)
    silence: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in regions:
        if start > cursor:
            silence.append((cursor, start))
        cursor = end
    if cursor < wav.duration_s:
        silence.append((cursor, wav.duration_s))
    durations = [end - start for start, end in silence]
    longest = max(durations, default=0.0)
    silent = sum(durations)
    return {
        "ratio": silent / wav.duration_s if wav.duration_s else 0.0,
        "longest_s": longest,
        "segments": silence,
    }


def clipping_ratio(wav: WavData) -> float:
    if len(wav.samples) == 0:
        return 0.0
    clipped = int(
        np.count_nonzero(
            np.abs(wav.samples) >= CHECK_THRESHOLDS["clipping_peak"]["value"]
        )
    )
    return clipped / len(wav.samples)


def chunk_at_silence(
    wav: WavData,
    max_s: float = CHECK_THRESHOLDS["speech_chunk_max_duration_s"]["value"],
) -> list[Chunk]:
    chunks: list[Chunk] = []
    cursor = 0.0
    duration = wav.duration_s
    silence_segments = silence_stats(wav)["segments"]
    silence_segments = silence_segments if isinstance(silence_segments, list) else []
    while cursor < duration:
        limit = min(cursor + max_s, duration)
        if limit >= duration:
            end = duration
        else:
            candidates = [
                segment_end
                for segment_start, segment_end in silence_segments
                if cursor + 1.0 <= segment_end <= limit
            ]
            end = max(candidates) if candidates else limit
        start_sample = int(round(cursor * wav.sr))
        end_sample = min(len(wav.samples), int(round(end * wav.sr)))
        chunks.append(Chunk(cursor, end, wav.samples[start_sample:end_sample]))
        cursor = end
    return chunks


def concat_wavs(wavs: list[WavData]) -> WavData:
    if not wavs:
        return WavData(samples=np.array([], dtype=np.float32), sr=24000)
    sr = wavs[0].sr
    pieces = [wav.samples for wav in wavs if wav.sr == sr]
    if not pieces:
        return WavData(samples=np.array([], dtype=np.float32), sr=sr)
    return WavData(samples=np.concatenate(pieces), sr=sr)
