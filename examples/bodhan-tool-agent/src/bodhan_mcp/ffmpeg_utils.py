"""FFmpeg utilities for audio conversion, extraction, chunking, and joining.

Tools must convert any input to 16 kHz mono WAV before sending to Indic-Transcribe,
and raise a clear error if ffmpeg is missing.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import wave


def find_ffmpeg() -> str:
    """Locates ffmpeg executable from PATH, WinGet directory, or environment variable."""
    if os.environ.get("FFMPEG_PATH"):
        custom = Path(os.environ["FFMPEG_PATH"])
        if custom.exists():
            return str(custom)

    path = shutil.which("ffmpeg")
    if path:
        return path

    # Common Windows WinGet install location
    winget_candidate = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
        / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        / "ffmpeg-9.1-full_build"
        / "bin"
        / "ffmpeg.exe"
    )
    if winget_candidate.exists():
        return str(winget_candidate)

    raise RuntimeError(
        "ffmpeg is required but was not found in PATH. Please install ffmpeg or set the FFMPEG_PATH environment variable."
    )


def find_ffprobe() -> str:
    """Locates ffprobe executable alongside ffmpeg."""
    ffmpeg_bin = find_ffmpeg()
    ffprobe_candidate = Path(ffmpeg_bin).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if ffprobe_candidate.exists():
        return str(ffprobe_candidate)
    probe_path = shutil.which("ffprobe")
    if probe_path:
        return probe_path
    raise RuntimeError("ffprobe was not found alongside ffmpeg.")


def get_audio_duration(audio_path: Path | str) -> float:
    """Returns duration of an audio file in seconds."""
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # Try standard wave module first for speed
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        pass

    # Fallback to ffprobe
    probe = find_ffprobe()
    cmd = [
        probe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def convert_to_wav_16k_mono(input_path: Path | str, output_path: Path | str) -> Path:
    """Converts any audio file (wav, mp3, m4a, etc.) to 16 kHz 16-bit mono WAV."""
    in_p = Path(input_path)
    out_p = Path(output_path)
    if not in_p.exists():
        raise FileNotFoundError(f"Input file not found: {in_p}")

    ffmpeg = find_ffmpeg()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(in_p),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(out_p),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out_p


def extract_audio_from_video(video_path: Path | str, output_path: Path | str) -> Path:
    """Extracts audio from a video file directly to 16 kHz 16-bit mono WAV."""
    in_p = Path(video_path)
    out_p = Path(output_path)
    if not in_p.exists():
        raise FileNotFoundError(f"Video file not found: {in_p}")

    ffmpeg = find_ffmpeg()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-i", str(in_p),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(out_p),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out_p


def join_audio_files(input_paths: list[Path | str], output_path: Path | str) -> Path:
    """Joins multiple audio files into a single WAV file using ffmpeg concat filter."""
    if not input_paths:
        raise ValueError("Cannot join an empty list of audio files.")

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if len(input_paths) == 1:
        shutil.copyfile(input_paths[0], out_p)
        return out_p

    ffmpeg = find_ffmpeg()
    cmd = [ffmpeg, "-y"]
    for p in input_paths:
        cmd.extend(["-i", str(p)])

    n = len(input_paths)
    inputs_spec = "".join([f"[{i}:a]" for i in range(n)])
    filter_complex = f"{inputs_spec}concat=n={n}:v=0:a=1[out]"
    cmd.extend(["-filter_complex", filter_complex, "-map", "[out]", str(out_p)])

    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out_p


def split_audio_at_silence(
    audio_path: Path | str,
    max_window_s: float = 30.0,
    output_dir: Path | str | None = None,
) -> list[Path]:
    """Splits audio into segments <= max_window_s (default 30s) using silence detection with time fallback."""
    in_p = Path(audio_path)
    duration = get_audio_duration(in_p)

    if duration <= max_window_s:
        return [in_p]

    if output_dir is None:
        out_dir = in_p.parent / f"{in_p.stem}_chunks"
    else:
        out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_ffmpeg()

    # Detect silence points
    cmd = [
        ffmpeg,
        "-i", str(in_p),
        "-af", "silencedetect=noise=-30dB:d=0.3",
        "-f", "null",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    silence_ends = [
        float(m.group(1))
        for m in re.finditer(r"silence_end:\s*([0-9.]+)", res.stderr)
    ]

    # Calculate split timestamps ensuring each interval <= max_window_s
    split_points = [0.0]
    last_point = 0.0

    for s_end in silence_ends:
        if s_end - last_point >= 15.0 and s_end - last_point <= max_window_s:
            split_points.append(s_end)
            last_point = s_end
        elif s_end - last_point > max_window_s:
            # Silence was too far; add intermediate split point
            while last_point + max_window_s < s_end:
                last_point += max_window_s
                split_points.append(last_point)
            split_points.append(s_end)
            last_point = s_end

    # Complete until end of audio
    while last_point + max_window_s < duration:
        last_point += max_window_s
        split_points.append(last_point)
    split_points.append(duration)

    # Export chunks
    chunks: list[Path] = []
    for i in range(len(split_points) - 1):
        start = split_points[i]
        end = split_points[i + 1]
        seg_dur = end - start
        if seg_dur <= 0.1:
            continue

        chunk_path = out_dir / f"chunk_{i:03d}_{start:.1f}s_{end:.1f}s.wav"
        seg_cmd = [
            ffmpeg,
            "-y",
            "-ss", str(start),
            "-i", str(in_p),
            "-t", str(seg_dur),
            "-c:a", "pcm_s16le",
            str(chunk_path),
        ]
        subprocess.run(seg_cmd, capture_output=True, text=True, check=True)
        chunks.append(chunk_path)

    return chunks
