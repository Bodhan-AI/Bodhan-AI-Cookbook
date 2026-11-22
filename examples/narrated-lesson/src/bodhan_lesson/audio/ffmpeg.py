"""WAV -> normalised, tagged MP3 with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def encode_mp3(wav: Path, mp3: Path, *, title: str, comment: str) -> None:
    """Loudness-normalise (-16 LUFS) and encode 24 kHz mono 64 kbps MP3 with ID3 tags."""
    command = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
               "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "24000", "-ac", "1",
               "-codec:a", "libmp3lame", "-b:a", "64k", "-metadata", f"title={title}",
               "-metadata", f"comment={comment}", str(mp3)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(result.stderr.strip()[:300])
