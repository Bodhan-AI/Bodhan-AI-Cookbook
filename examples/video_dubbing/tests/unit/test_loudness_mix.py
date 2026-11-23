"""
Unit tests for loudness normalization and audio mixing.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

from bodhan_dub.media.loudness import (
    apply_peak_limiter,
    measure_loudness,
    measure_true_peak_dbtp,
    normalize_to_target_lufs,
)
from bodhan_dub.media.mix import mix_dubbed_audio, mute_intervals_with_crossfade


def test_mute_intervals_with_crossfade():
    sr = 48000
    # 2 seconds of constant tone at 1.0
    audio = np.ones(sr * 2, dtype=np.float32)
    # Mute from 0.5s to 1.5s
    muted = mute_intervals_with_crossfade(audio, [(0.5, 1.5)], sample_rate=sr, fade_ms=40.0)

    # Before fade-out (at 0.2s) should be 1.0
    assert muted[int(0.2 * sr)] == 1.0
    # Middle of muted span (at 1.0s) should be exactly 0.0
    assert muted[int(1.0 * sr)] == 0.0
    # After fade-in (at 1.8s) should be 1.0
    assert muted[int(1.8 * sr)] == 1.0


def test_loudness_normalization_and_peak_limiting():
    sr = 48000
    dur = 2.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    # Loud sine wave
    audio = 0.5 * np.sin(2 * np.pi * 1000 * t)

    measured = measure_loudness(audio, sample_rate=sr)
    assert -40.0 < measured < 0.0

    normalized, final_lufs = normalize_to_target_lufs(audio, target_lufs=-16.0, sample_rate=sr)
    # Target is -16.0 ± 1.0 LUFS
    assert abs(final_lufs - (-16.0)) <= 1.0
    # Peak must not exceed -1.0 dBTP (~0.8912)
    assert np.max(np.abs(normalized)) <= 0.892


def test_peak_limiter():
    # Audio with peak exceeding -1.0 dBTP (e.g. 1.2)
    hot_audio = np.array([-1.2, 0.5, 1.2], dtype=np.float32)
    limited = apply_peak_limiter(hot_audio, max_peak_dbtp=-1.0)
    assert np.max(np.abs(limited)) <= 0.892


def test_mix_dubbed_audio_flow(tmp_path: Path):
    sr = 48000
    dur = 3.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    bg_audio = 0.1 * np.sin(2 * np.pi * 200 * t)
    speech_stem = np.zeros(int(sr * dur), dtype=np.float32)
    speech_stem[int(sr * 1.0) : int(sr * 2.0)] = 0.4 * np.sin(2 * np.pi * 500 * t[: int(sr * 1.0)])

    orig_wav = tmp_path / "orig.wav"
    sf.write(str(orig_wav), bg_audio, sr)

    out_wav = tmp_path / "dubbed_mixed.wav"
    lufs = mix_dubbed_audio(
        original_audio_path=orig_wav,
        speech_stem_48k=speech_stem,
        speech_intervals_to_mute=[(1.0, 2.0)],
        output_wav_path=out_wav,
        target_lufs=-16.0,
        sample_rate=sr,
    )

    assert out_wav.exists()
    assert abs(lufs - (-16.0)) <= 1.5


def test_true_peak_limiter_polyphase():
    """B5: True peak limiter with 4x polyphase oversampling strictly keeps peaks <= -1.0 dBTP."""
    # Synthetic signal that creates inter-sample peaks
    sr = 48000
    dur = 1.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    hot_signal = 1.2 * np.sin(2 * np.pi * 12000 * t)  # high amplitude, high frequency

    limited = apply_peak_limiter(hot_signal, max_peak_dbtp=-1.0)
    measured_tp = measure_true_peak_dbtp(limited)

    # True peak must be <= -0.95 dBTP (within floating precision of -1.0 dBTP)
    assert measured_tp <= -0.95


def test_mix_unmuted_skipped_slots(tmp_path: Path):
    """B5: Slots where dubbing was skipped keep original audio unmuted."""
    sr = 48000
    dur = 4.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)

    # Original audio has distinct tone at 440 Hz
    orig_speech = 0.5 * np.sin(2 * np.pi * 440 * t)
    orig_wav = tmp_path / "orig_two_slots.wav"
    sf.write(str(orig_wav), orig_speech, sr)

    # Slot 1 (0.5..1.5s) was dubbed, so speech stem has audio there
    speech_stem = np.zeros(int(sr * dur), dtype=np.float32)
    speech_stem[int(sr * 0.5) : int(sr * 1.5)] = 0.4 * np.sin(2 * np.pi * 880 * t[: int(sr * 1.0)])

    # Slot 2 (2.0..3.0s) was SKIPPED: not present in speech_intervals_to_mute!
    out_wav = tmp_path / "dubbed_with_skipped.wav"
    mix_dubbed_audio(
        original_audio_path=orig_wav,
        speech_stem_48k=speech_stem,
        speech_intervals_to_mute=[(0.5, 1.5)],  # Slot 2 (2.0..3.0) is NOT muted!
        output_wav_path=out_wav,
        target_lufs=-16.0,
        sample_rate=sr,
    )

    mixed, _ = sf.read(str(out_wav), dtype="float32")

    # In slot 2 (2.0s..3.0s), original speech must be preserved (not muted!)
    max_amp_slot2 = np.max(np.abs(mixed[int(2.2 * sr) : int(2.8 * sr)]))
    assert max_amp_slot2 > 0.05  # unmuted, real speech kept!
