"""ffmpeg / ffprobe plumbing: probing, decoding, silence-aware windowing, concatenation, loudness normalisation."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

from . import config
from .errors import ToolFailure


def _binary(name: str) -> str:
    env = os.environ.get(f"{name.upper()}_BINARY")
    found = env if env and Path(env).exists() else shutil.which(name)
    if not found:
        raise ToolFailure("MISSING_DEPENDENCY", f"{name} is not installed or not on PATH.",
                          hint=f"Install ffmpeg (provides {name}) or set {name.upper()}_BINARY to its full path.")
    return found


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL)  # never inherit the MCP stdio pipe as stdin
    except subprocess.TimeoutExpired as exc:
        raise ToolFailure("PROCESSING_TIMEOUT", f"{Path(cmd[0]).name} timed out.", retryable=True, hint="Try a shorter file.") from exc


def ffprobe(path: Path) -> dict:
    cp = _run([_binary("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], timeout=60)
    if cp.returncode != 0:
        raise ToolFailure("UNREADABLE_MEDIA", f"ffprobe could not read {path.name}: {cp.stderr.strip()[:200]}",
                          hint="The file may be corrupt or not audio/video. Use inspect_media to check its type.")
    return json.loads(cp.stdout or "{}")


def duration_of(path: Path) -> float:
    info = ffprobe(path)
    d = (info.get("format") or {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                d = s["duration"]
                break
    return float(d or 0.0)


def decode_to_wav(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    cp = _run([_binary("ffmpeg"), "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)])
    if cp.returncode != 0 or not dst.exists() or dst.stat().st_size < 100:
        raise ToolFailure("UNREADABLE_MEDIA", f"Could not decode audio from {src.name}: {cp.stderr.strip()[:200]}",
                          hint="Check the file has an audio stream (inspect_media).")
    return dst


def silence_midpoints(wav: Path, noise_db: int = -35, min_silence_s: float = 0.3) -> list[float]:
    """Midpoints (seconds) of detected silences: the safest places to cut."""
    cp = _run([_binary("ffmpeg"), "-nostdin", "-v", "info", "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", cp.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", cp.stderr)]
    return [max((s + e) / 2, 0.0) for s, e in zip(starts, ends, strict=False)]


def plan_windows(duration: float, silences: list[float], max_len: float = config.ASR_WINDOW_MAX_S,
                 min_len: float = config.ASR_WINDOW_MIN_S) -> list[tuple[float, float]]:
    """Cut [0, duration] into windows <= max_len, preferring the latest silence midpoint inside each window.

    Falls back to a hard cut at max_len only when a window contains no usable silence.
    """
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration - 1e-6:
        if duration - start <= max_len:
            windows.append((start, duration))
            break
        candidates = [s for s in silences if start + min_len <= s <= start + max_len]
        end = max(candidates) if candidates else start + max_len
        windows.append((start, end))
        start = end
    return windows


def write_window(wav: Path, start: float, end: float, dst: Path) -> Path:
    """Copy [start, end) out of a PCM WAV with the stdlib (no extra ffmpeg process)."""
    with wave.open(str(wav), "rb") as src:
        rate = src.getframerate()
        src.setpos(min(int(start * rate), src.getnframes()))
        frames = src.readframes(max(int((end - start) * rate), 0))
        with wave.open(str(dst), "wb") as out:
            out.setparams(src.getparams())
            out.writeframes(frames)
    return dst


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def concat_wavs(parts: list[Path], dst: Path, gap_s: float = 0.25) -> Path:
    """Concatenate PCM WAVs of identical format with a short pause between them."""
    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
    gap = b"\x00\x00" * int(params.framerate * gap_s) * params.nchannels
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        for i, p in enumerate(parts):
            with wave.open(str(p), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (params.framerate, params.nchannels, params.sampwidth):
                    raise ToolFailure("UPSTREAM_ERROR", "Synthesised parts had different audio formats.", retryable=True, hint="Retry.")
                out.writeframes(w.readframes(w.getnframes()))
            if i < len(parts) - 1:
                out.writeframes(gap)
    return dst


def finalize_audio(src_wav: Path, dst: Path, title: str) -> tuple[Path, str | None]:
    """Loudness-normalise and write dst (.mp3 or .wav) labelled as AI-generated. Returns (path, note)."""
    meta = ["-metadata", f"title={title}", "-metadata", "comment=AI-generated speech (Bodhan Indic-Speak). Not a human recording.",
            "-metadata", "artist=Bodhan Indic-Speak (AI-generated)"]
    base = [_binary("ffmpeg"), "-nostdin", "-y", "-v", "error", "-i", str(src_wav), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    if dst.suffix == ".mp3":
        cp = _run(base + ["-ar", "24000", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4"] + meta + [str(dst)])
        if cp.returncode == 0 and dst.exists():
            return dst, None
        dst = dst.with_suffix(".wav")
        note = "mp3 encoder unavailable; wrote WAV instead"
    else:
        note = None
    cp = _run(base + ["-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le"] + meta + [str(dst)])
    if cp.returncode != 0 or not dst.exists():
        shutil.copyfile(src_wav, dst)
        note = (note or "") + " loudness normalisation failed; wrote un-normalised audio"
    return dst, note


def sniff_magic(path: Path) -> str:
    """Guess the real format from file bytes (not the extension)."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    sigs = [(b"RIFF", "wav"), (b"ID3", "mp3"), (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"), (b"fLaC", "flac"), (b"OggS", "ogg"),
            (b"%PDF", "pdf"), (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"), (b"BM", "bmp"), (b"II*\x00", "tiff"),
            (b"MM\x00*", "tiff"), (b"\x1aE\xdf\xa3", "mkv/webm")]
    for sig, name in sigs:
        if head.startswith(sig):
            return name
    if head[4:8] == b"ftyp":
        return "mp4/m4a"
    return "unknown"
