"""Pitch-preserving time-stretch with ffmpeg's atempo filter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def apply_speed(src: str | Path, dst: str | Path, speed: float) -> Path:
    """Write src sped up by `speed` (1.1 = 10% faster, shorter) to dst. Output: mono PCM16 WAV, same rate."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if abs(speed - 1.0) < 0.005:
        shutil.copyfile(src, dst)
        return dst
    if not 0.5 <= speed <= 2.0:
        raise ValueError(f"speed {speed} outside atempo range 0.5-2.0")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
                        "-filter:a", f"atempo={speed:.4f}", "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg atempo failed: {r.stderr.strip()}")
    return dst
