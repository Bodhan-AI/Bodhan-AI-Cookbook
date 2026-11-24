import numpy as np

from bodhan_eval.combiner import combine
from bodhan_eval.config import load_thresholds
from bodhan_eval.models import Evidence
from bodhan_eval.speech import SignalFeatures, _exact_audio_repeat, signal_evidence


def test_exact_audio_repeat_finds_copied_audio_but_ignores_silence_and_unique_audio():
    rng = np.random.default_rng(42)
    audio = rng.uniform(-0.3, 0.3, 4000).astype("float32")
    assert _exact_audio_repeat(audio, 1000) is None
    assert _exact_audio_repeat(np.zeros(8000, dtype="float32"), 1000) is None
    assert _exact_audio_repeat(np.concatenate((audio, audio)), 1000) is not None


def test_audio_loop_requires_transcript_corroboration():
    thresholds = load_thresholds()
    feat = SignalFeatures(duration_s=4, exact_audio_repeat=(0, 2, 0.5))
    signal = signal_evidence("hi", "hello world", feat, thresholds)
    assert not combine(signal, thresholds, "hi").flags
    signal.append(Evidence(check="s5_repetition", category="speech_repetition", strength="medium",
                           noisy=True, details={"repeated_ngram": "hello world"}))
    assert any(flag.category == "speech_repetition" for flag in combine(signal, thresholds, "hi").flags)
