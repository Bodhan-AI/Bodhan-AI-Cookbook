import io

import numpy as np
import soundfile as sf

from bodhan_eval.checks.audio import check_audio, load_audio, spoken_chars
from bodhan_eval.params import Params

P = Params.defaults()
SR = 24000
TEXT = "समुद्र तल पर पानी सौ डिग्री पर उबलता है"  # 33 spoken chars
CPS = 13.0


def speechlike(seconds: float, seed: int = 0) -> np.ndarray:
    """Harmonic signal with a syllable-rate envelope: low spectral flatness, like voiced speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    x = sum((0.6 / k) * np.sin(k * phase) for k in range(1, 15))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4 * t + rng.uniform(0, 6))
    return (0.3 * x * env / np.max(np.abs(x))).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


def expected_len() -> float:
    return spoken_chars(TEXT) / CPS


def run(x):
    return check_audio(x, SR, TEXT, P, CPS)


def test_clean_audio_has_no_flags():
    x = np.concatenate([silence(0.2), speechlike(expected_len()), silence(0.3)])
    res = run(x)
    assert res.flags == [], [f.evidence for f in res.flags]
    assert 0.9 < res.signals["duration_ratio"] < 1.1


def test_long_internal_silence_is_flagged_with_time_span():
    half = expected_len() / 2
    x = np.concatenate([speechlike(half), silence(2.5), speechlike(half, 1)])
    res = run(x)
    gaps = [f for f in res.flags if "silence in the middle" in f.evidence]
    assert len(gaps) == 1 and gaps[0].category == "speech_garbled"
    t0, t1 = gaps[0].time_span
    assert abs(t0 - half) < 0.1 and abs(t1 - (half + 2.5)) < 0.1
    assert gaps[0].span is not None


def test_noise_burst_is_flagged():
    rng = np.random.default_rng(3)
    noise = (0.3 * rng.standard_normal(int(1.0 * SR))).astype(np.float32)
    half = expected_len() / 2
    x = np.concatenate([speechlike(half), noise, speechlike(half, 2)])
    res = run(x)
    assert any("noise-like" in f.evidence for f in res.flags)


def test_clipping_is_flagged():
    x = np.clip(speechlike(expected_len()) * 6, -1, 1)
    res = run(x)
    clip = [f for f in res.flags if "clipped" in f.evidence]
    assert clip and clip[0].severity == "major"


def test_short_audio_suggests_omission():
    res = run(speechlike(expected_len() * 0.35))
    assert [f.category for f in res.flags] == ["speech_omission"]


def test_long_audio_suggests_repetition():
    res = run(speechlike(expected_len() * 2.5))
    assert [f.category for f in res.flags] == ["speech_repetition"]


def test_silent_and_empty_audio():
    assert run(silence(3.0)).flags[0].severity == "critical"
    assert run(silence(0.01)).flags[0].severity == "critical"


def test_level_independent():
    x = np.concatenate([silence(0.2), speechlike(expected_len()) * 0.05, silence(0.2)])
    assert run(x).flags == []


def test_load_audio_from_bytes_and_stereo():
    stereo = np.stack([speechlike(1.0), speechlike(1.0)], axis=1)
    buf = io.BytesIO()
    sf.write(buf, stereo, SR, format="WAV", subtype="PCM_16")
    x, sr = load_audio(buf.getvalue())
    assert sr == SR and x.ndim == 1 and len(x) == SR
