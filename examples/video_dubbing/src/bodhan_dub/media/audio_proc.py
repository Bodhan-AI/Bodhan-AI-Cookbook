"""
Audio signal processing utilities: silence trimming, clipping detection,
resampling from 24kHz to 48kHz, time-stretching with ffmpeg atempo, and crossfade stitching.
"""

from __future__ import annotations

import io
import math
import subprocess
from typing import List

import numpy as np
import scipy.signal
import soundfile as sf


def resample_to_48k(samples: np.ndarray, orig_sr: int) -> np.ndarray:
    """
    Rational polyphase resampling from orig_sr to 48,000 Hz float32.
    """
    if len(samples) == 0:
        return np.zeros(0, dtype=np.float32)
    if orig_sr == 48000:
        return samples.astype(np.float32)

    # Convert to float32 normalized in [-1.0, 1.0]
    if samples.dtype == np.int16:
        float_samples = samples.astype(np.float32) / 32768.0
    else:
        float_samples = samples.astype(np.float32)

    target_sr = 48000
    g = math.gcd(int(orig_sr), target_sr)
    up = target_sr // g
    down = int(orig_sr) // g

    resampled = scipy.signal.resample_poly(float_samples, up=up, down=down)
    return resampled.astype(np.float32)


def resample_24k_to_48k(samples: np.ndarray) -> np.ndarray:
    """
    High-fidelity 2x upsampling from 24,000 Hz to 48,000 Hz float32.
    """
    return resample_to_48k(samples, 24000)


def trim_edge_silence(
    samples: np.ndarray,
    threshold_db: float = -45.0,
    min_keep_samples: int = 240,  # 10ms at 24kHz
) -> np.ndarray:
    """
    Trims leading and trailing silence below threshold_db from a mono audio array.
    """
    if len(samples) == 0:
        return samples

    abs_samples = np.abs(samples)
    threshold_amp = 10.0 ** (threshold_db / 20.0)

    active_indices = np.where(abs_samples > threshold_amp)[0]
    if len(active_indices) == 0:
        return np.zeros(0, dtype=samples.dtype)

    start_idx = max(0, active_indices[0] - min_keep_samples)
    end_idx = min(len(samples), active_indices[-1] + min_keep_samples)

    return samples[start_idx:end_idx]


def check_clipping(samples: np.ndarray, threshold: float = 0.999) -> bool:
    """Detects whether audio exceeds digital ceiling indicating harsh distortion."""
    if len(samples) == 0:
        return False
    peak = float(np.max(np.abs(samples)))
    return peak >= threshold


def detect_internal_silence(
    samples: np.ndarray,
    sample_rate: int = 24000,
    min_silence_s: float = 1.5,
    threshold_db: float = -45.0,
) -> bool:
    """
    B8: Vectorized unnatural pause detection using numpy without Python sample loops.
    Detects if there is a silent pause longer than min_silence_s inside speech.
    """
    if len(samples) == 0:
        return False

    threshold_amp = 10.0 ** (threshold_db / 20.0)
    window_len = int(min_silence_s * sample_rate)

    if len(samples) < window_len:
        return False

    is_silent = (np.abs(samples) < threshold_amp).astype(np.int32)

    # Use run-length encoding via diff on zero-padded boundaries
    padded = np.pad(is_silent, (1, 1), mode="constant", constant_values=0)
    diffs = np.diff(padded)
    run_starts = np.where(diffs == 1)[0]
    run_ends = np.where(diffs == -1)[0]

    if len(run_starts) == 0:
        return False

    run_lengths = run_ends - run_starts
    return bool(np.any(run_lengths >= window_len))


def time_stretch_audio(
    samples: np.ndarray,
    speed: float,
    sample_rate: int = 48000,
) -> np.ndarray:
    """
    Time-stretches audio by factor `speed` (1.0x to 1.2x) using ffmpeg atempo filter.
    Preserves pitch accurately. If speed == 1.0, returns copy.
    """
    if abs(speed - 1.0) < 0.005 or len(samples) == 0:
        return samples.copy()

    # Clamp speed to allowed stretch range [0.85, 1.20]
    clamped_speed = max(0.85, min(1.20, speed))

    # Write input WAV to memory buffer
    in_buf = io.BytesIO()
    sf.write(in_buf, samples, sample_rate, format="WAV", subtype="PCM_16")
    in_bytes = in_buf.getvalue()

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        "pipe:0",
        "-filter:a",
        f"atempo={clamped_speed:.4f}",
        "-f",
        "wav",
        "pipe:1",
    ]

    proc = subprocess.run(cmd, input=in_bytes, capture_output=True, check=True)
    out_buf = io.BytesIO(proc.stdout)
    stretched, _ = sf.read(out_buf, dtype="float32")
    return stretched


async def async_time_stretch_audio(
    samples: np.ndarray,
    speed: float,
    sample_rate: int = 48000,
) -> np.ndarray:
    """B8: Offloads atempo time stretching to a background thread."""
    import asyncio
    return await asyncio.to_thread(time_stretch_audio, samples, speed, sample_rate)


def crossfade_concat(
    segments: List[np.ndarray],
    crossfade_ms: int = 10,
    sample_rate: int = 24000,
) -> np.ndarray:
    """
    B8: Vectorized concatenation of audio arrays with short linear crossfades
    using pure numpy operations instead of Python list concatenation.
    """
    valid = [s.astype(np.float32) for s in segments if len(s) > 0]
    if not valid:
        return np.zeros(0, dtype=np.float32)
    if len(valid) == 1:
        return valid[0]

    xfade_samples = int((crossfade_ms / 1000.0) * sample_rate)
    fade_out = np.linspace(1.0, 0.0, xfade_samples, dtype=np.float32)
    fade_in = np.linspace(0.0, 1.0, xfade_samples, dtype=np.float32)

    result = valid[0]

    for nxt in valid[1:]:
        if len(result) < xfade_samples or len(nxt) < xfade_samples:
            result = np.concatenate([result, nxt])
            continue

        # Overlap crossfade
        overlap_a = result[-xfade_samples:]
        overlap_b = nxt[:xfade_samples]
        crossfaded = (overlap_a * fade_out) + (overlap_b * fade_in)

        # Concatenate: result prefix + crossfade region + next suffix
        result = np.concatenate([result[:-xfade_samples], crossfaded, nxt[xfade_samples:]])

    return result.astype(np.float32)
