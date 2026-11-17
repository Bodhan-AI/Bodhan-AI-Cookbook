from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Literal

from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.process import run_process, stderr_tail

OutputFormat = Literal["wav", "mp3"]

AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".webm", ".mp4", ".amr", ".wma"}
)
SAMPLE_RATE = 16_000
SPEECH_SAMPLE_RATE = 24_000
_WAV_HEADER_BYTES = 44
_ENCODERS: dict[str, list[str]] = {
    "wav": ["-c:a", "pcm_s16le"],
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
}
_SILENCE_RE = re.compile(r"silence_(start|end): (-?\d+(?:\.\d+)?)")
_FORMAT_HINT = "Use an audio or media file with an audio track, such as WAV, MP3, M4A, FLAC, OGG or OPUS."


def require_binaries() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise _missing_binaries()
    return ffmpeg, ffprobe


def _missing_binaries() -> BodhanToolError:
    return BodhanToolError(
        "internal_error",
        "ffmpeg and ffprobe are required for transcription but could not be run.",
        hint="Install ffmpeg (for example `sudo apt install ffmpeg` or `brew install ffmpeg`) and restart the server.",
    )


def check_audio_extension(path: Path) -> None:
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise BodhanToolError(
            "unsupported_file",
            f"{path.name} is not a supported audio or media file.",
            hint=f"Supported extensions: {', '.join(sorted(AUDIO_EXTENSIONS))}.",
            details={"extension": path.suffix},
        )


def sniff_audio_format(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    return None


def _ensure_absent(dest: Path) -> None:
    if dest.exists():
        raise BodhanToolError("file_exists", f"{dest.name} already exists.", details={"path": dest.name})


def _unreadable(path: Path, reason: str, stderr: bytes = b"") -> BodhanToolError:
    details = {"path": path.name}
    tail = stderr_tail(stderr, path)
    if tail:
        details["ffmpeg"] = tail
    return BodhanToolError("unsupported_file", f"{path.name} {reason}.", hint=_FORMAT_HINT, details=details)


async def probe_duration(path: Path, *, deadline: float) -> float:
    _, ffprobe = require_binaries()
    code, stdout, stderr = await run_process(
        [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
        deadline,
        "Reading the audio file",
    )
    if code != 0:
        raise _unreadable(path, "could not be read as audio", stderr)
    try:
        data = json.loads(stdout)
    except ValueError as exc:
        raise _unreadable(path, "could not be read as audio") from exc
    if not any(stream.get("codec_type") == "audio" for stream in data.get("streams") or []):
        raise _unreadable(path, "has no audio track")
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration <= 0:
        raise _unreadable(path, "has no measurable duration")
    return duration


async def detect_silences(
    path: Path,
    *,
    deadline: float,
    noise_db: float = -35.0,
    min_silence_s: float = 0.3,
) -> list[tuple[float, float]]:
    ffmpeg, _ = require_binaries()
    code, _, stderr = await run_process(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
            "-f",
            "null",
            "-",
        ],
        deadline,
        "Silence detection",
    )
    if code != 0:
        raise _unreadable(path, "could not be decoded", stderr)
    silences: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in _SILENCE_RE.findall(stderr.decode("utf-8", errors="replace")):
        moment = max(0.0, float(value))
        if kind == "start":
            start = moment
        elif start is not None:
            if moment > start:
                silences.append((start, moment))
            start = None
    return silences


def plan_chunks(
    duration_s: float,
    silences: list[tuple[float, float]],
    *,
    max_chunk_s: float = 25.0,
    min_chunk_s: float = 5.0,
) -> list[tuple[float, float]]:
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("duration_s must be a positive number")
    if min_chunk_s <= 0 or max_chunk_s < 2 * min_chunk_s:
        raise ValueError("max_chunk_s must be at least twice min_chunk_s, and both positive")
    cuts = sorted(
        (start + end) / 2
        for start, end in silences
        if math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration_s
    )
    spans: list[tuple[float, float]] = []
    start = 0.0
    while duration_s - start > max_chunk_s:
        lower = start + min_chunk_s
        upper = min(start + max_chunk_s, duration_s - min_chunk_s)
        candidates = [cut for cut in cuts if lower <= cut <= upper]
        end = candidates[-1] if candidates else upper
        spans.append((start, end))
        start = end
    spans.append((start, duration_s))
    return spans


async def extract_chunks(
    path: Path,
    spans: list[tuple[float, float]],
    workdir: Path,
    *,
    deadline: float,
) -> list[Path]:
    ffmpeg, _ = require_binaries()
    chunks: list[Path] = []
    for index, (start, end) in enumerate(spans):
        dest = workdir / f"chunk_{index:04d}.wav"
        _ensure_absent(dest)
        code, _, stderr = await run_process(
            [
                ffmpeg,
                "-hide_banner",
                "-v",
                "error",
                "-n",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{end - start:.3f}",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(dest),
            ],
            deadline,
            f"Converting audio chunk {index + 1} of {len(spans)}",
        )
        if code != 0 or not dest.is_file() or dest.stat().st_size <= _WAV_HEADER_BYTES:
            raise _unreadable(path, f"could not be converted at {start:.1f}-{end:.1f} s", stderr)
        chunks.append(dest)
    return chunks


async def join_audio(
    parts: list[Path],
    dest: Path,
    *,
    deadline: float,
    fmt: OutputFormat = "wav",
    gap_s: float = 0.25,
    comment: str | None = None,
) -> Path:
    if not parts:
        raise ValueError("parts must not be empty")
    if gap_s < 0 or not math.isfinite(gap_s):
        raise ValueError("gap_s must be a finite non-negative number")
    ffmpeg, _ = require_binaries()
    _ensure_absent(dest)
    inputs: list[str] = []
    filters: list[str] = []
    for index, part in enumerate(parts):
        inputs += ["-i", str(part)]
        pad = f",apad=pad_dur={gap_s}" if gap_s and index < len(parts) - 1 else ""
        filters.append(
            f"[{index}:a]aresample={SPEECH_SAMPLE_RATE},aformat=sample_fmts=s16:channel_layouts=mono{pad}[a{index}]"
        )
    labels = "".join(f"[a{index}]" for index in range(len(parts)))
    filters.append(f"{labels}concat=n={len(parts)}:v=0:a=1[out]")
    metadata = ["-metadata", f"comment={comment}"] if comment else []
    code, _, stderr = await run_process(
        [
            ffmpeg,
            "-hide_banner",
            "-v",
            "error",
            "-n",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
            "-map_metadata",
            "-1",
            *metadata,
            *_ENCODERS[fmt],
            str(dest),
        ],
        deadline,
        "Joining audio",
    )
    if code != 0 or not dest.is_file() or dest.stat().st_size <= _WAV_HEADER_BYTES:
        raise BodhanToolError(
            "internal_error",
            f"Joining {len(parts)} audio pieces into {dest.name} failed.",
            details={"ffmpeg": stderr_tail(stderr, dest)},
        )
    return dest
