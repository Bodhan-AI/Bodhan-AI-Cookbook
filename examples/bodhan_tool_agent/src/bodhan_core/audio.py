"""
Audio convenience functions for chunking, probing, converting, and stitching.
Directly compatible with unit tests and MCP tools.
"""
from __future__ import annotations

import math
import pathlib
import wave
from typing import Any

from bodhan_core.media import audio as media_audio


def probe_audio(path: pathlib.Path | str) -> float:
    """Return the audio duration in seconds as a float."""
    info = media_audio.probe_audio(pathlib.Path(path))
    return float(info.get("duration_seconds", 0.0))


def convert_to_wav(src: pathlib.Path | str, dst: pathlib.Path | str) -> pathlib.Path:
    """Convert input file to mono 16 kHz WAV at dst."""
    return media_audio.convert_to_wav(pathlib.Path(src), pathlib.Path(dst))


def chunk_audio(
    wav_path: pathlib.Path | str,
    out_dir: pathlib.Path | str,
    max_duration_secs: float = 30.0,
    overlap_secs: float = 0.0,
) -> list[pathlib.Path]:
    """Chunk audio file so every chunk is <= max_duration_secs."""
    return media_audio.chunk_audio(
        pathlib.Path(wav_path),
        pathlib.Path(out_dir),
        max_secs=max_duration_secs,
        overlap_secs=overlap_secs,
    )


def stitch_audio(
    chunks: list[pathlib.Path | str],
    out: pathlib.Path | str,
    pause_ms: int = 100,
) -> pathlib.Path:
    """Concatenate chunks into a single output WAV."""
    chunk_paths = [pathlib.Path(c) for c in chunks]
    return media_audio.stitch_audio(chunk_paths, pathlib.Path(out), pause_ms=pause_ms)
