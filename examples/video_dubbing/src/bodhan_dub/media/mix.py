"""
Audio mixing pipeline.
Ducks/mutes original speech regions with smooth 40ms crossfades while preserving
background music, ambience, and skipped segment audio, then mixes dubbed speech stem
and normalizes to -16 LUFS integrated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import scipy.signal
import soundfile as sf

from bodhan_dub.media.loudness import (
    apply_true_peak_limiter,
    measure_loudness,
    normalize_to_target_lufs,
)


class MixResult(float):
    """
    Subclasses float for backwards compatibility while allowing tuple unpacking
    and attribute access for stem_lufs.
    """

    def __new__(cls, final_lufs: float, stem_lufs: float):
        instance = super().__new__(cls, final_lufs)
        instance.final_lufs = final_lufs
        instance.stem_lufs = stem_lufs
        return instance

    def __iter__(self):
        yield self.final_lufs
        yield self.stem_lufs

    def __getitem__(self, index):
        return (self.final_lufs, self.stem_lufs)[index]


def compute_union_mute_intervals(
    source_speech_spans: List[Tuple[float, float]],
    placed_spans: Optional[List[Tuple[float, float]]] = None,
    skipped_spans: Optional[List[Tuple[float, float]]] = None,
) -> List[Tuple[float, float]]:
    """
    A2: Computes mute intervals:
    - Mutes only VAD regions that overlap a PLACED (non-skipped) segment, merged with placed spans.
    - VAD regions with no placed segment (ASR gap, hallucination-filtered, translation failure, skipped) stay un-muted.
    """
    valid_placed: List[Tuple[float, float]] = []
    if placed_spans:
        for s, e in placed_spans:
            if e > s:
                valid_placed.append((round(float(s), 3), round(float(e), 3)))

    if not valid_placed:
        return []

    # 1. Select only VAD regions that overlap at least one placed segment
    overlapping_vad: List[Tuple[float, float]] = []
    for s, e in source_speech_spans:
        v_s = round(float(s), 3)
        v_e = round(float(e), 3)
        if v_e <= v_s:
            continue
        # Overlap condition: max(v_s, p_s) < min(v_e, p_e)
        if any(max(v_s, p_s) < min(v_e, p_e) for p_s, p_e in valid_placed):
            overlapping_vad.append((v_s, v_e))

    # 2. Merge overlapping VAD regions with placed spans
    all_spans = overlapping_vad + valid_placed
    all_spans.sort(key=lambda x: x[0])

    merged: List[Tuple[float, float]] = []
    curr_s, curr_e = all_spans[0]
    for s, e in all_spans[1:]:
        if s <= curr_e:
            curr_e = max(curr_e, e)
        else:
            merged.append((curr_s, curr_e))
            curr_s, curr_e = s, e
    merged.append((curr_s, curr_e))

    # 3. Subtract skipped spans (punch holes)
    if skipped_spans:
        for skip_s, skip_e in skipped_spans:
            if skip_e <= skip_s:
                continue
            new_merged: List[Tuple[float, float]] = []
            for m_s, m_e in merged:
                if skip_e <= m_s or skip_s >= m_e:
                    new_merged.append((m_s, m_e))
                else:
                    if m_s < skip_s:
                        new_merged.append((m_s, skip_s))
                    if m_e > skip_e:
                        new_merged.append((skip_e, m_e))
            merged = new_merged

    return merged


def mute_intervals_with_crossfade(
    audio: np.ndarray,
    mute_spans: List[Tuple[float, float]],
    sample_rate: int = 48000,
    fade_ms: float = 40.0,
) -> np.ndarray:
    """
    Applies smooth equal-power (cos/sin) gain envelope to mute speech regions in original audio.
    fade_ms: duration of fade-in and fade-out (default 40.0ms).
    """
    out = audio.copy()
    fade_samples = max(1, int(round((fade_ms / 1000.0) * sample_rate)))
    num_samples = len(audio)

    # 1.0 = full volume, 0.0 = muted
    gain = np.ones(num_samples, dtype=np.float32)

    # Precalculate equal-power fade curves: cos for fade-out, sin for fade-in
    t_curve = np.linspace(0.0, np.pi / 2.0, fade_samples, endpoint=False, dtype=np.float32)
    fade_out_curve = np.cos(t_curve)  # 1.0 down to ~0.0
    fade_in_curve = np.sin(t_curve)   # 0.0 up to 1.0

    for start_s, end_s in mute_spans:
        s_idx = int(round(start_s * sample_rate))
        e_idx = int(round(end_s * sample_rate))

        s_idx = max(0, min(num_samples, s_idx))
        e_idx = max(0, min(num_samples, e_idx))

        if e_idx <= s_idx:
            continue

        # Equal-power fade out before start
        fade_out_start = max(0, s_idx - fade_samples)
        fade_out_len = s_idx - fade_out_start
        if fade_out_len > 0:
            curve = fade_out_curve[-fade_out_len:]
            gain[fade_out_start:s_idx] = np.minimum(gain[fade_out_start:s_idx], curve)

        # Full mute during speech
        gain[s_idx:e_idx] = 0.0

        # Equal-power fade in after end
        fade_in_end = min(num_samples, e_idx + fade_samples)
        fade_in_len = fade_in_end - e_idx
        if fade_in_len > 0:
            curve = fade_in_curve[:fade_in_len]
            gain[e_idx:fade_in_end] = np.minimum(gain[e_idx:fade_in_end], curve)

    # E5: Memory-bounded in-place gain application
    if out.ndim == 1:
        out *= gain
        return out
    else:
        out *= gain[:, np.newaxis]
        return out


def mix_dubbed_audio(
    original_audio_path: Path,
    speech_stem_48k: np.ndarray,
    speech_intervals_to_mute: List[Tuple[float, float]],
    output_wav_path: Path,
    target_lufs: float = -16.0,
    sample_rate: int = 48000,
    separate_music_vocal_path: Optional[Path] = None,
    placed_spans: Optional[List[Tuple[float, float]]] = None,
    skipped_spans: Optional[List[Tuple[float, float]]] = None,
) -> float:
    """
    B3 & B11: Mutes original speech and placed spans, mixes dubbed speech stem,
    normalizes loudness to -16 LUFS (±1) with true-peak ceiling (-1 dBTP),
    and writes final 48kHz audio WAV.
    Returns final integrated LUFS.
    """
    output_wav_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Read original 48kHz audio
    orig_data, orig_sr = sf.read(str(original_audio_path), dtype="float32")
    if orig_sr != sample_rate:
        raise ValueError(
            f"Original audio sample rate {orig_sr} does not match required {sample_rate}"
        )

    is_stereo = orig_data.ndim > 1 and orig_data.shape[1] >= 2
    total_samples = len(orig_data)

    # Match stem length to original audio length
    stem = speech_stem_48k.copy()
    if len(stem) < total_samples:
        pad_len = total_samples - len(stem)
        stem = np.pad(stem, (0, pad_len), mode="constant")
    elif len(stem) > total_samples:
        stem = stem[:total_samples]

    # D7: Per-segment loudness normalization before stem normalization
    if np.max(np.abs(stem)) > 1e-4:
        if placed_spans:
            for s, e in placed_spans:
                idx_s = max(0, int(round(s * sample_rate)))
                idx_e = min(len(stem), int(round(e * sample_rate)))
                if idx_e > idx_s and np.max(np.abs(stem[idx_s:idx_e])) > 1e-4:
                    seg_data = stem[idx_s:idx_e]
                    p_max = float(np.max(np.abs(seg_data)))
                    if p_max > 0.01:
                        target_peak = 0.50
                        gain = float(np.clip(target_peak / p_max, 0.5, 2.0))
                        stem[idx_s:idx_e] = seg_data * gain

        norm_stem, _ = normalize_to_target_lufs(stem, target_lufs=target_lufs, sample_rate=sample_rate)
        norm_stem = apply_true_peak_limiter(norm_stem, max_peak_dbtp=-1.0, sample_rate=sample_rate)
        stem = norm_stem
        stem_lufs = measure_loudness(stem, sample_rate)
    else:
        stem_lufs = -70.0

    # B11: Compute union of source speech + placed spans, minus skipped spans
    effective_mute_spans = compute_union_mute_intervals(
        source_speech_spans=speech_intervals_to_mute,
        placed_spans=placed_spans,
        skipped_spans=skipped_spans,
    )

    # 2. Mute speech intervals in original audio (or vocal stem if separated)
    if separate_music_vocal_path and Path(separate_music_vocal_path).exists():
        try:
            vocal_data, vocal_sr = sf.read(str(separate_music_vocal_path), dtype="float32")
            # Resample vocal data if sample rate differs
            if vocal_sr != sample_rate:
                gcd = int(np.gcd(vocal_sr, sample_rate))
                up = sample_rate // gcd
                down = vocal_sr // gcd
                if vocal_data.ndim == 1:
                    vocal_data = scipy.signal.resample_poly(vocal_data, up, down).astype(np.float32)
                else:
                    vocal_data = scipy.signal.resample_poly(vocal_data, up, down, axis=0).astype(np.float32)

            # Match channels
            if is_stereo and vocal_data.ndim == 1:
                vocal_data = np.column_stack([vocal_data, vocal_data])
            elif not is_stereo and vocal_data.ndim > 1:
                vocal_data = vocal_data[:, 0]

            # Match sample length
            if len(vocal_data) < total_samples:
                vocal_data = np.pad(vocal_data, ((0, total_samples - len(vocal_data)), (0, 0)) if is_stereo else (0, total_samples - len(vocal_data)))
            elif len(vocal_data) > total_samples:
                vocal_data = vocal_data[:total_samples]

            # Duck only vocal stem, background instrumental remains unmuted
            muted_vocal = mute_intervals_with_crossfade(
                audio=vocal_data,
                mute_spans=effective_mute_spans,
                sample_rate=sample_rate,
                fade_ms=40.0,
            )
            muted_bg = orig_data - vocal_data + muted_vocal
        except Exception:
            # Fall back cleanly to full original audio ducking
            muted_bg = mute_intervals_with_crossfade(
                audio=orig_data,
                mute_spans=effective_mute_spans,
                sample_rate=sample_rate,
                fade_ms=40.0,
            )
    else:
        muted_bg = mute_intervals_with_crossfade(
            audio=orig_data,
            mute_spans=effective_mute_spans,
            sample_rate=sample_rate,
            fade_ms=40.0,
        )

    # 3. Sum speech stem with background (E5: memory-bounded in-place addition)
    if is_stereo:
        muted_bg[:, 0] += stem
        muted_bg[:, 1] += stem
        mixed = muted_bg[:, :2]
    else:
        if muted_bg.ndim > 1:
            mono_bg = muted_bg[:, 0]
            mono_bg += stem
            mixed = mono_bg
        else:
            muted_bg += stem
            mixed = muted_bg

    # 4. Multi-stage loudness normalization & limiting:
    # Ensure -16 ± 1 LUFS and peak <= -1.0 dBTP
    normalized, final_lufs = normalize_to_target_lufs(
        mixed,
        target_lufs=target_lufs,
        sample_rate=sample_rate,
    )
    limited = apply_true_peak_limiter(normalized, max_peak_dbtp=-1.0, sample_rate=sample_rate)

    # 5. Measure and adjust up to 3 times if outside [-17, -15] LUFS
    curr_lufs = measure_loudness(limited, sample_rate)
    for _ in range(3):
        if -17.0 <= curr_lufs <= -15.0 or curr_lufs <= -60.0:
            break
        delta_db = target_lufs - curr_lufs
        limited = apply_true_peak_limiter(
            limited * (10.0 ** (np.clip(delta_db, -6.0, 6.0) / 20.0)),
            max_peak_dbtp=-1.0,
            sample_rate=sample_rate,
        )
        curr_lufs = measure_loudness(limited, sample_rate)

    final_lufs = curr_lufs

    # 6. Write to output WAV (48kHz float32/PCM_16)
    sf.write(str(output_wav_path), limited, sample_rate, subtype="PCM_16")

    return MixResult(round(final_lufs, 2), round(stem_lufs, 2))


async def async_mix_dubbed_audio(*args, **kwargs) -> MixResult:
    """B8: Offloads mixing, muting, and file I/O to a background thread."""
    return await asyncio.to_thread(mix_dubbed_audio, *args, **kwargs)

