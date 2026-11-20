"""Unit tests for speech synthesis, chunk splitting, and loudness normalization."""


import numpy as np
from bodhan_lesson.synthesizer import (
    create_wav_bytes,
    normalize_loudness,
    parse_wav_pcm16_samples,
    split_narration_chunks,
)


def test_split_narration_chunks():
    hindi_text = "कोण दो प्रकार के होते हैं। न्यून कोण और समकोण। यहाँ x का मान 180 अंश है।"
    chunks = split_narration_chunks(hindi_text, max_chars=40)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 45


def test_wav_packing_and_parsing():
    sample_rate = 24000
    orig_samples = (np.sin(np.linspace(0, 100, sample_rate)) * 10000).astype(np.int16)
    wav_bytes = create_wav_bytes(orig_samples, sample_rate)

    parsed_samples, sr = parse_wav_pcm16_samples(wav_bytes)
    assert sr == sample_rate
    assert len(parsed_samples) == len(orig_samples)
    assert np.array_equal(parsed_samples, orig_samples)


def test_loudness_normalization():
    sample_rate = 24000
    t = np.linspace(0, 2, sample_rate * 2)
    quiet_samples = (0.1 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    normalized, measured_lufs = normalize_loudness(quiet_samples, sample_rate, target_lufs=-16.0)

    assert len(normalized) == len(quiet_samples)
    # Measured lufs should be close to -16 LUFS (+- 1.0)
    assert -17.0 <= measured_lufs <= -15.0
