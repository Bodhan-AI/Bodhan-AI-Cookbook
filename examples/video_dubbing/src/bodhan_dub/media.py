"""ffmpeg / ffprobe helpers. All media work shells out to ffmpeg, a documented system dependency."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm"})
ASR_SAMPLE_RATE = 16_000


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration_s: float
    has_video: bool
    has_audio: bool


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise MediaError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (see README)")


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr.strip()[-500:]}")
    return proc.stdout


def probe(path: Path) -> MediaInfo:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                "-of", "json", str(path)])
    data = json.loads(out)
    kinds = {s.get("codec_type") for s in data.get("streams", [])}
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError(f"could not read duration of {path}") from exc
    return MediaInfo(duration_s=duration, has_video="video" in kinds, has_audio="audio" in kinds)


def extract_audio(video: Path, out_wav: Path, sample_rate: int = ASR_SAMPLE_RATE) -> Path:
    """Decode the first audio track to mono PCM16 WAV, the format Indic-Transcribe documents."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(video), "-vn", "-map", "0:a:0",
          "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(out_wav)])
    return out_wav
