"""
VAD-aware audio splitting using pydub silence detection.

Splits at silence boundaries so chunks stay under max_seconds and sentences
are not cut mid-word. Falls back to hard splits if no silence is found.
"""
from __future__ import annotations

from pathlib import Path

from pydub import AudioSegment
from pydub.silence import split_on_silence

_CHUNK_DIR_SUFFIX = "_chunks"


def chunk_audio(audio_path: Path, max_seconds: float = 28.0) -> list[Path]:
    """Split an audio file into chunks each under max_seconds.

    Returns a list of paths to the chunk files. If the original is already
    short enough, returns [audio_path] without creating any new files.
    """
    path = Path(audio_path)
    audio = AudioSegment.from_file(str(path))
    max_ms = int(max_seconds * 1000)

    if len(audio) <= max_ms:
        return [path]

    out_dir = path.parent / (path.stem + _CHUNK_DIR_SUFFIX)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Split on silence; keep 200ms of silence at chunk edges for natural speech
    raw = split_on_silence(
        audio,
        min_silence_len=400,
        silence_thresh=-40,
        keep_silence=200,
    )

    # Fallback if no non-silent portions were found (e.g. pure silence or continuous sound)
    if not raw:
        raw = [audio[i : i + max_ms] for i in range(0, len(audio), max_ms)]

    # Merge short chunks up to max_ms; force-split any that exceed it
    merged: list[AudioSegment] = []
    current = AudioSegment.empty()
    for piece in raw:
        # If this single piece is already larger than max_ms, slice it
        if len(piece) > max_ms:
            if len(current) > 0:
                merged.append(current)
                current = AudioSegment.empty()
            for sub_i in range(0, len(piece), max_ms):
                merged.append(piece[sub_i : sub_i + max_ms])
            continue

        if len(current) + len(piece) <= max_ms:
            current += piece
        else:
            if len(current) > 0:
                merged.append(current)
            current = piece
    if len(current) > 0:
        merged.append(current)

    paths: list[Path] = []
    for i, seg in enumerate(merged):
        p = out_dir / f"chunk_{i:04d}.wav"
        seg.export(str(p), format="wav")
        paths.append(p)
    return paths
