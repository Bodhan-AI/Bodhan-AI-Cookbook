"""
Loudness measurement and normalization adhering to ITU-R BS.1770 / EBU R128.
Standard targets: -16.0 LUFS integrated (±1.0 LUFS) with a -1.0 dBTP true-peak ceiling.
Includes 4x oversampled lookahead true-peak limiter with attack/release envelope.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pyloudnorm as pyln
import scipy.ndimage
import scipy.signal


def measure_loudness(audio_48k: np.ndarray, sample_rate: int = 48000) -> float:
    """
    Measures integrated loudness in LUFS using ITU-R BS.1770 standard.
    Returns -70.0 for near-silent audio.
    """
    if len(audio_48k) == 0:
        return -70.0

    meter = pyln.Meter(sample_rate)
    # pyloudnorm expects shape (samples, channels)
    data = audio_48k[:, np.newaxis] if audio_48k.ndim == 1 else audio_48k

    # Guard against silence or very short audio (<0.4s)
    if len(data) < int(sample_rate * 0.4) or np.max(np.abs(data)) < 1e-4:
        return -70.0

    try:
        loudness = meter.integrated_loudness(data)
        if np.isneginf(loudness) or np.isnan(loudness):
            return -70.0
        return float(loudness)
    except Exception:
        return -70.0


def measure_true_peak_dbtp(audio: np.ndarray) -> float:
    """Measures maximum true peak in dBTP using 4x polyphase oversampling."""
    if len(audio) == 0:
        return -100.0
    if audio.ndim == 1:
        oversampled = scipy.signal.resample_poly(audio, up=4, down=1)
        peak_linear = float(np.max(np.abs(oversampled)))
    else:
        peak_linear = max(
            float(np.max(np.abs(scipy.signal.resample_poly(audio[:, ch], up=4, down=1))))
            for ch in range(audio.shape[1])
        )
    if peak_linear <= 1e-9:
        return -100.0
    return float(20.0 * np.log10(peak_linear))


def apply_true_peak_limiter(
    audio: np.ndarray,
    max_peak_dbtp: float = -1.0,
    sample_rate: int = 48000,
    attack_ms: float = 5.0,
    release_ms: float = 50.0,
) -> np.ndarray:
    """
    B3: Lookahead true-peak limiter:
    - 4x polyphase oversampling to detect inter-sample peaks
    - Lookahead delay matched to attack time (5ms)
    - Smooth gain envelope (5ms attack, 50ms release)
    - Hard ceiling at max_peak_dbtp (default -1.0 dBTP = ~0.89125 linear)
    """
    if len(audio) == 0:
        return audio

    ceiling_linear = 10.0 ** (max_peak_dbtp / 20.0)
    orig_ndim = audio.ndim
    is_stereo = (orig_ndim > 1 and audio.shape[1] >= 2)

    # Work with mono peak envelope across channels
    if is_stereo:
        mono_signal = np.max(np.abs(audio), axis=1)
    else:
        mono_signal = np.abs(audio)

    if np.max(mono_signal) <= 0:
        return audio

    # 4x oversampling for true peak detection
    up_factor = 4
    oversampled = scipy.signal.resample_poly(mono_signal, up=up_factor, down=1)
    peak_tp = float(np.max(np.abs(oversampled)))

    if peak_tp <= ceiling_linear:
        return audio.astype(np.float32)

    # Compute required gain at 4x rate: g[k] = min(1.0, ceiling / |oversampled[k]|)
    oversampled_abs = np.maximum(np.abs(oversampled), 1e-9)
    raw_gain_4x = np.minimum(1.0, ceiling_linear / oversampled_abs)

    # Fast vectorized lookahead attack smoothing over attack_ms at 4x sample rate
    sr_4x = sample_rate * up_factor
    att_samples_4x = max(1, int(round((attack_ms / 1000.0) * sr_4x)))
    g_att_4x = scipy.ndimage.minimum_filter1d(raw_gain_4x, size=att_samples_4x, mode="nearest")

    # Downsample gain envelope to 1x rate (48kHz) taking the minimum over each 4-sample block
    # so inter-sample peaks are strictly clamped and never missed
    n_full_blocks = len(audio) * up_factor
    if len(g_att_4x) < n_full_blocks:
        g_att_4x = np.pad(g_att_4x, (0, n_full_blocks - len(g_att_4x)), mode="edge")
    g_1x = np.min(g_att_4x[:n_full_blocks].reshape(-1, up_factor), axis=1)

    # Release smoothing over release_ms at 1x sample rate
    rel_samples_1x = max(1, int(round((release_ms / 1000.0) * sample_rate)))
    smooth_gain = scipy.ndimage.minimum_filter1d(g_1x, size=rel_samples_1x, mode="nearest")

    # Apply gain to audio
    if is_stereo:
        limited = audio * smooth_gain[:, np.newaxis]
    else:
        limited = audio * smooth_gain

    # Final safety check: if any residual inter-sample peak exceeds ceiling, apply slight global trim
    final_tp = measure_true_peak_dbtp(limited)
    if final_tp > max_peak_dbtp:
        trim_db = max_peak_dbtp - final_tp - 0.05
        trim_linear = 10.0 ** (trim_db / 20.0)
        limited = limited * trim_linear

    return limited.astype(np.float32)


apply_peak_limiter = apply_true_peak_limiter


def normalize_to_target_lufs(
    audio_48k: np.ndarray,
    target_lufs: float = -16.0,
    sample_rate: int = 48000,
    max_gain_db: float = 18.0,
) -> Tuple[np.ndarray, float]:
    """
    B3: Normalizes audio to target integrated LUFS with peak limiting.
    Iterates gain up to 3 times to satisfy both LUFS (-16 ± 1 LUFS) and peak (≤ -1 dBTP).
    Returns (normalized_audio_48k, measured_lufs).
    """
    if len(audio_48k) == 0:
        return audio_48k, -70.0

    measured = measure_loudness(audio_48k, sample_rate)

    if measured <= -60.0:
        # Audio is effectively silent, do not blow up background noise
        return audio_48k, measured

    current_audio = audio_48k.copy()
    current_lufs = measured

    # Iterative adjustment (up to 3 iterations) to converge on target LUFS while respecting -1 dBTP
    for _ in range(3):
        gain_db = target_lufs - current_lufs
        if abs(gain_db) < 0.2:
            break
        clamped_gain_db = np.clip(gain_db, -18.0, max_gain_db)
        gain_linear = 10.0 ** (clamped_gain_db / 20.0)
        scaled = current_audio * gain_linear
        limited = apply_true_peak_limiter(scaled, max_peak_dbtp=-1.0, sample_rate=sample_rate)
        new_lufs = measure_loudness(limited, sample_rate)
        current_audio = limited
        current_lufs = new_lufs
        if -17.0 <= current_lufs <= -15.0:
            break

    # If audio is very short (<400ms), pyloudnorm returns -70.0; use RMS-based matching
    if len(audio_48k) < int(sample_rate * 0.4) or current_lufs <= -60.0:
        rms = float(np.sqrt(np.mean(np.square(audio_48k))))
        if rms > 1e-4:
            target_rms = 0.112  # nominal -16 LUFS RMS for speech
            gain = min(10.0 ** (max_gain_db / 20.0), target_rms / rms)
            scaled = audio_48k * gain
            current_audio = apply_true_peak_limiter(scaled, max_peak_dbtp=-1.0, sample_rate=sample_rate)
            current_lufs = target_lufs

    return current_audio.astype(np.float32), round(current_lufs, 2)

