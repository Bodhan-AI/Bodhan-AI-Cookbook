"""
Audio processing: probing, format conversion, chunking (<30s), and stitching.
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from bodhan_core.errors import BodhanError, ErrorCode

log = logging.getLogger(__name__)

_HAVE_FFMPEG = shutil.which("ffmpeg") is not None
_HAVE_FFPROBE = shutil.which("ffprobe") is not None


def probe_audio(path: Path) -> dict[str, Any]:
    """
    Probe audio file to extract duration, sample rate, channels, and format.
    Tries ffprobe first; falls back to standard library wave for WAV files.
    """
    path = Path(path)
    if not path.exists():
        raise BodhanError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"Audio file not found: {path}",
            how_to_fix="Ensure file exists at the specified path.",
        )

    # 1. Try ffprobe if available
    if _HAVE_FFPROBE:
        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_streams",
                    "-show_format",
                    str(path),
                ],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout:
                info = json.loads(proc.stdout.decode("utf-8", errors="replace"))
                fmt = info.get("format", {})
                duration = float(fmt.get("duration", 0.0))
                channels = 1
                sample_rate = 16000
                for stream in info.get("streams", []):
                    if stream.get("codec_type") == "audio":
                        channels = int(stream.get("channels", 1))
                        sample_rate = int(stream.get("sample_rate", 16000))
                        if duration == 0.0 and "duration" in stream:
                            duration = float(stream["duration"])
                        break
                return {
                    "duration_seconds": round(duration, 3),
                    "channels": channels,
                    "sample_rate": sample_rate,
                    "format_name": fmt.get("format_name", path.suffix.lstrip(".")),
                    "size_bytes": path.stat().st_size,
                }
        except Exception as exc:
            log.debug("ffprobe failed on %s: %s", path, exc)

    # 2. Fallback for WAV via standard library wave module
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                n_frames = wf.getnframes()
                rate = wf.getframerate()
                duration = n_frames / float(rate) if rate > 0 else 0.0
                return {
                    "duration_seconds": round(duration, 3),
                    "channels": wf.getnchannels(),
                    "sample_rate": rate,
                    "format_name": "wav",
                    "size_bytes": path.stat().st_size,
                }
        except Exception as exc:
            log.debug("wave module failed on %s: %s", path, exc)

    raise BodhanError(
        code=ErrorCode.UNSUPPORTED_FORMAT,
        message=f"Could not probe audio file: {path.name}",
        how_to_fix="Ensure file is a valid audio format and ffprobe is installed.",
    )


def convert_to_wav(input_path: Path, output_path: Path) -> Path:
    """
    Convert an audio or video file to 16 kHz mono WAV using ffmpeg.
    If ffmpeg is not available and input is WAV, copies the file.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not _HAVE_FFMPEG:
        if input_path.suffix.lower() == ".wav":
            shutil.copyfile(input_path, output_path)
            return output_path
        raise BodhanError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message="ffmpeg is not installed on PATH for audio format conversion.",
            how_to_fix="Install ffmpeg or provide 16 kHz mono WAV directly.",
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", "16000",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    if proc.returncode != 0:
        raise BodhanError(
            code=ErrorCode.UNSUPPORTED_FORMAT,
            message=f"ffmpeg conversion failed: {proc.stderr.decode('utf-8', errors='replace')[:200]}",
            how_to_fix="Ensure input audio file is valid.",
        )

    return output_path


def chunk_audio(
    wav_path: Path,
    out_dir: Path,
    max_secs: float = 28.0,
    overlap_secs: float = 0.5,
) -> list[Path]:
    """
    Split a WAV file into segments each strictly under max_secs (default 28s).
    Returns list of chunk Paths in chronological order.
    """
    wav_path = Path(wav_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    info = probe_audio(wav_path)
    total_duration = info["duration_seconds"]

    if total_duration <= max_secs:
        return [wav_path]

    chunks: list[Path] = []
    # Calculate intervals
    step = max_secs - overlap_secs
    num_chunks = math.ceil(total_duration / step)

    for i in range(num_chunks):
        start = i * step
        if start >= total_duration:
            break
        duration = min(max_secs, total_duration - start)
        chunk_file = out_dir / f"chunk_{i:04d}.wav"

        # Try ffmpeg slice if available
        if _HAVE_FFMPEG:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss", f"{start:.3f}",
                "-t", f"{duration:.3f}",
                "-i", str(wav_path),
                "-acodec", "copy",
                str(chunk_file),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
                if proc.returncode == 0 and chunk_file.exists():
                    chunks.append(chunk_file)
                    continue
            except Exception:
                pass

        # Fallback slice using standard library wave
        try:
            with wave.open(str(wav_path), "rb") as r:
                params = r.getparams()
                rate = r.getframerate()
                start_frame = int(start * rate)
                num_frames = int(duration * rate)
                r.setpos(min(start_frame, r.getnframes()))
                frames = r.readframes(num_frames)

                with wave.open(str(chunk_file), "wb") as w:
                    w.setparams(params)
                    w.writeframes(frames)
                chunks.append(chunk_file)
        except Exception as exc:
            raise BodhanError(
                code=ErrorCode.UPSTREAM_ERROR,
                message=f"Failed to chunk audio: {exc}",
                how_to_fix="Ensure audio file is not corrupt.",
            )

    return chunks


def stitch_audio(
    chunk_paths: list[Path],
    output_path: Path,
    pause_ms: int = 250,
) -> Path:
    """
    Concatenate multiple WAV chunks into a single audio file with optional silence in between.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not chunk_paths:
        raise BodhanError(
            code=ErrorCode.INVALID_ARGUMENT,
            message="No audio chunks provided to stitch.",
            how_to_fix="Provide at least one audio chunk.",
        )

    if len(chunk_paths) == 1:
        import shutil
        shutil.copyfile(chunk_paths[0], output_path)
        return output_path

    # Use python wave module to combine
    try:
        first = wave.open(str(chunk_paths[0]), "rb")
        params = first.getparams()
        rate = first.getframerate()
        nchannels = first.getnchannels()
        sampwidth = first.getsampwidth()
        first.close()

        silence_frames = b"\x00" * int(rate * (pause_ms / 1000.0) * nchannels * sampwidth)

        with wave.open(str(output_path), "wb") as out_wf:
            out_wf.setparams(params)
            for i, chunk in enumerate(chunk_paths):
                with wave.open(str(chunk), "rb") as in_wf:
                    out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))
                if i < len(chunk_paths) - 1 and pause_ms > 0:
                    out_wf.writeframes(silence_frames)

        return output_path
    except Exception as exc:
        raise BodhanError(
            code=ErrorCode.UPSTREAM_ERROR,
            message=f"Failed to stitch audio files: {exc}",
            how_to_fix="Ensure all input chunks are valid WAV files with identical parameters.",
        )
