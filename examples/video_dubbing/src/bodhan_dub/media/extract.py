"""
Audio extraction utilities for bodhan_dub.
Extracts 16kHz mono WAV for ASR/VAD and 48kHz WAV preserving original audio for mixing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


class MediaExtractError(Exception):
    """Raised when audio extraction fails."""

    pass


def extract_audio_for_asr(video_path: Path, out_wav_path: Path) -> Path:
    """
    Extracts a 16 kHz mono 16-bit PCM WAV file optimized for VAD and ASR.
    Downmixes stereo or 5.1 cleanly and resets negative timestamps.
    """
    video_path = Path(video_path)
    out_wav_path = Path(out_wav_path)
    out_wav_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-avoid_negative_ts",
        "make_zero",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(out_wav_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise MediaExtractError(
            f"Failed to extract ASR audio from {video_path}: {e.stderr.strip()}"
        )
    except Exception as e:
        raise MediaExtractError(f"Unexpected error extracting audio from {video_path}: {str(e)}")

    if not out_wav_path.exists() or out_wav_path.stat().st_size == 0:
        raise MediaExtractError(f"Extracted audio file is empty or missing: {out_wav_path}")

    return out_wav_path


def extract_audio_original(
    video_path: Path,
    out_wav_path: Path,
    sample_rate: int = 48000,
    target_duration: Optional[float] = None,
) -> Path:
    """
    B12: Extracts full-quality uncompressed audio from the video at 48kHz for the background mix.
    If target_duration is specified and extracted audio is shorter (e.g. 2s gap in source video),
    pads audio with silence to exact video duration.
    """
    import numpy as np
    import soundfile as sf

    video_path = Path(video_path)
    out_wav_path = Path(out_wav_path)
    out_wav_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-avoid_negative_ts",
        "make_zero",
        "-i",
        str(video_path),
        "-vn",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(out_wav_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise MediaExtractError(
            f"Failed to extract original audio from {video_path}: {e.stderr.strip()}"
        )
    except Exception as e:
        raise MediaExtractError(
            f"Unexpected error extracting original audio from {video_path}: {str(e)}"
        )

    if not out_wav_path.exists() or out_wav_path.stat().st_size == 0:
        raise MediaExtractError(
            f"Extracted original audio file is empty or missing: {out_wav_path}"
        )

    # Pad shorter audio stream if target_duration provided
    if target_duration and target_duration > 0.0:
        try:
            data, sr = sf.read(str(out_wav_path), dtype="float32")
            target_samples = int(round(target_duration * sr))
            if len(data) < target_samples:
                pad_len = target_samples - len(data)
                if data.ndim == 1:
                    padded = np.pad(data, (0, pad_len), mode="constant")
                else:
                    padded = np.pad(data, ((0, pad_len), (0, 0)), mode="constant")
                sf.write(str(out_wav_path), padded, sr, subtype="PCM_16")
        except Exception:
            pass

    return out_wav_path
