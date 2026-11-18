"""Audio measurement and safe, bounded media conversion. No recognition models."""

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def read_audio(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Audio file does not exist: {path.name}")
    if path.stat().st_size > 200_000_000:
        raise ValueError("Audio exceeds the 200 MB per-item limit")
    try:
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "converted.wav"
            command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
                       "-ac", "1", "-ar", "24000", str(target)]
            subprocess.run(command, check=True, capture_output=True, timeout=90)
            audio, rate = sf.read(target, dtype="float32", always_2d=True)
    if not len(audio) or rate <= 0 or not np.isfinite(audio).all():
        raise ValueError("Audio has no finite samples")
    if len(audio) / rate > 900:
        raise ValueError("Audio exceeds the 15-minute per-item limit")
    return audio.mean(axis=1), rate


def audio_stats(audio, rate):
    frame = max(1, int(rate * 0.02))
    levels = [float(np.sqrt(np.mean(audio[i:i + frame] ** 2)))
              for i in range(0, len(audio), frame)]
    silent = [level < 0.005 for level in levels]
    longest = current = 0
    for value in silent:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return {"duration_seconds": round(len(audio) / rate, 4),
            "rms": round(float(np.sqrt(np.mean(audio ** 2))), 6),
            "peak": round(float(np.max(np.abs(audio))), 6),
            "clipping_fraction": round(float(np.mean(np.abs(audio) >= 0.999)), 6),
            "silence_fraction": round(sum(silent) / len(silent), 4),
            "longest_silence_seconds": round(min(len(audio) / rate, longest * 0.02), 4)}


def write_chunks(audio, rate, folder, seconds=28):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    width = int(rate * seconds)
    for i, start in enumerate(range(0, len(audio), width)):
        path = folder / f"chunk-{i:04d}.wav"
        sf.write(path, audio[start:start + width], rate, subtype="PCM_16")
        paths.append(path)
    return paths


def text_chunks(text, limit=180):
    """Break on whitespace where possible, preserving every character."""
    chunks = []
    while len(text) > limit:
        end = text.rfind(" ", 0, limit + 1)
        if end < limit // 3:
            end = limit
        chunks.append(text[:end])
        text = text[end:]
    if text:
        chunks.append(text)
    return chunks
