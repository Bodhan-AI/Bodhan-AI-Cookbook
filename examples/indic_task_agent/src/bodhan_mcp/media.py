"""Audio and file helpers built on ffmpeg/ffprobe. No Bodhan calls and no cost here."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from bodhan_mcp.errors import BodhanError

MAX_CHUNK_S = 28.0  # the transcribe limit is ~30 s; keep a safety margin
MIN_CHUNK_S = 10.0
_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".amr", ".wma", ".webm"}
_VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _need(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise BodhanError(
            "ffmpeg_missing", f"'{binary}' is not installed or not on PATH.",
            hint="Install ffmpeg (https://ffmpeg.org) and restart the terminal.",
        )
    return path


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise BodhanError("media_error", f"{Path(cmd[0]).name} failed: {p.stderr.strip()[-300:]}")
    return p


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in _IMAGE_EXT:
        return "image"
    if ext in {".txt", ".md", ".srt", ".json", ".csv"}:
        return "text"
    if ext in _VIDEO_EXT or ext in _AUDIO_EXT:
        return "av"  # decided by ffprobe below
    return "unknown"


def inspect_media(path: str | Path) -> dict[str, Any]:
    """Describe a file: kind, duration, codecs, size. Safe to call on any file."""
    p = Path(path)
    if not p.exists():
        raise BodhanError("file_not_found", f"File not found: {p}", hint="Check the path.")
    info: dict[str, Any] = {"path": str(p), "name": p.name, "size_bytes": p.stat().st_size,
                            "kind": kind_of(p)}
    if info["kind"] == "av":
        out = _run([_need("ffprobe"), "-v", "error", "-print_format", "json",
                    "-show_format", "-show_streams", str(p)]).stdout
        d = json.loads(out)
        streams = d.get("streams", [])
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        info["kind"] = "video" if video else "audio" if audio else "unknown"
        info["duration_s"] = round(float(d.get("format", {}).get("duration") or 0), 2)
        if audio:
            info["audio"] = {"codec": audio.get("codec_name"),
                             "sample_rate": int(audio.get("sample_rate") or 0),
                             "channels": audio.get("channels")}
        if video:
            info["video"] = {"codec": video.get("codec_name"),
                             "width": video.get("width"), "height": video.get("height")}
    elif info["kind"] == "text":
        info["chars"] = len(p.read_text(encoding="utf-8", errors="replace"))
    return info


def to_wav(src: Path, dst: Path, rate: int = 16000) -> Path:
    """Convert any audio (or the audio track of a video) to 16-bit mono WAV."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([_need("ffmpeg"), "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
          "-c:a", "pcm_s16le", str(dst)])
    return dst


def find_silences(wav: Path, noise_db: int = -35, min_silence_s: float = 0.3) -> list[tuple[float, float]]:
    """Return (start, end) of each silent stretch, using ffmpeg's silencedetect filter."""
    p = subprocess.run(
        [_need("ffmpeg"), "-i", str(wav), "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
         "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    return list(zip(starts, ends))  # a trailing unmatched start is ignored


def plan_chunks(duration: float, silences: list[tuple[float, float]],
                max_len: float = MAX_CHUNK_S, min_len: float = MIN_CHUNK_S) -> list[tuple[float, float]]:
    """Decide where to cut. Prefer the middle of a silence so words are not split.
    Pure function: easy to test without any audio."""
    if duration <= max_len:
        return [(0.0, duration)]
    mids = [(a + b) / 2 for a, b in silences]
    chunks, start = [], 0.0
    while duration - start > max_len:
        window = [m for m in mids if start + min_len <= m <= start + max_len]
        cut = max(window) if window else start + max_len  # last silence in range, else hard cut
        chunks.append((start, cut))
        start = cut
    chunks.append((start, duration))
    return chunks


def cut_wav(src: Path, dst: Path, start: float, end: float) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([_need("ffmpeg"), "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
          "-c:a", "pcm_s16le", str(dst)])
    return dst


def prepare_for_transcribe(src: Path, work_dir: Path) -> list[dict[str, Any]]:
    """Any audio/video file -> list of <=28 s WAV chunks with their start times."""
    wav = to_wav(src, work_dir / f"{src.stem}.16k.wav")
    with wave.open(str(wav)) as w:
        duration = w.getnframes() / w.getframerate()
    plan = plan_chunks(duration, find_silences(wav) if duration > MAX_CHUNK_S else [])
    if len(plan) == 1:
        return [{"path": wav, "start": 0.0, "end": duration}]
    return [{"path": cut_wav(wav, work_dir / f"{src.stem}.part{i:03d}.wav", a, b), "start": a, "end": b}
            for i, (a, b) in enumerate(plan)]


def join_wavs(parts: list[bytes], dst: Path, gap_ms: int = 250) -> Path:
    """Join WAV byte blobs (all the same format) into one file, with a short pause between."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    params = None
    frames: list[bytes] = []
    for blob in parts:
        with wave.open(io.BytesIO(blob)) as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (
                    params.nchannels, params.sampwidth, params.framerate):
                raise BodhanError("audio_format_mismatch", "Speech parts have different formats.")
            frames.append(w.readframes(w.getnframes()))
    if params is None:
        raise BodhanError("no_audio", "Nothing to join.")
    gap = b"\x00" * (params.sampwidth * params.nchannels * int(params.framerate * gap_ms / 1000))
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        out.writeframes(gap.join(frames))
    return dst


def wav_to_mp3(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([_need("ffmpeg"), "-y", "-i", str(src), "-codec:a", "libmp3lame", "-q:a", "4", str(dst)])
    return dst