"""Audio signal checks and back-transcription alignment."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import pytest

from bodhan_eval.audio.analysis import analyze
from bodhan_eval.audio.io import Audio, load
from bodhan_eval.checks.speech import SpeechRateModel, check_audio_signal, check_transcription
from bodhan_eval.config import Thresholds
from bodhan_eval.inject.audio_ops import garble, repeat, truncate
from bodhan_eval.models import Category
from bodhan_eval.text.speakable import to_speakable
from conftest import make_ctx, speechlike

TH = Thresholds()
TEXT = "आज मौसम बहुत अच्छा है और हम सब पार्क में खेलने जाएंगे। शाम को हम घर लौटकर खाना खाएंगे।"
SAMPLE = Path(__file__).resolve().parents[3] / "sample_data" / "sample_hindi.wav"


def audio_ctx(audio: Audio, text: str = TEXT, transcript: str | None = None):
    ctx = make_ctx("Today the weather is nice and we will play in the park. In the evening we will eat.", text)
    ctx.audio = audio
    ctx.speakable = to_speakable(text)
    ctx.transcript = transcript
    ctx.audio_stats = analyze(audio)
    return ctx


def speech_cats(ctx) -> list[str]:
    return [s.category.value for s in ctx.signals if s.category.area == "speech"]


def test_clean_speechlike_signal_has_no_flags():
    ctx = audio_ctx(speechlike(5.0))
    check_audio_signal(ctx, TH, None)
    assert speech_cats(ctx) == []


def test_long_internal_silence_is_garbled_with_time_span():
    ctx = audio_ctx(speechlike(6.0, pauses=[(2.0, 4.0)]))
    check_audio_signal(ctx, TH, None)
    sig = next(s for s in ctx.signals if s.category == Category.SPEECH_GARBLED)
    assert 1.8 <= sig.audio_span[0] <= 2.2 and sig.span is not None


def test_clipping_and_noise_are_detected():
    x = speechlike(4.0).samples.copy()
    x[24000:48000] = np.clip(x[24000:48000] * 10, -1, 1)
    ctx = audio_ctx(Audio(x, 24000))
    check_audio_signal(ctx, TH, None)
    assert any("clipped" in s.message for s in ctx.signals)

    y = speechlike(4.0).samples.copy()
    y[36000:60000] = np.random.default_rng(1).normal(0, 0.3, 24000)
    ctx = audio_ctx(Audio(y, 24000))
    check_audio_signal(ctx, TH, None)
    assert any("noise" in s.message for s in ctx.signals)


def test_silent_audio_is_a_critical_omission():
    ctx = audio_ctx(Audio(np.zeros(24000, dtype=np.float32), 24000))
    check_audio_signal(ctx, TH, None)
    assert ctx.signals[0].category == Category.SPEECH_OMISSION and ctx.signals[0].severity.value == "critical"


def test_duration_far_below_expected_suggests_truncation():
    ctx = audio_ctx(speechlike(1.5))
    check_audio_signal(ctx, TH, SpeechRateModel({"hi": [14.0] * 6}))
    assert "speech_omission" in speech_cats(ctx)


@pytest.mark.skipif(not SAMPLE.exists(), reason="cookbook sample clip not present")
def test_real_hindi_clip_clean_and_injected():
    clip = load(SAMPLE)
    clean = audio_ctx(clip)
    check_audio_signal(clean, TH, None)
    assert speech_cats(clean) == [], [s.message for s in clean.signals]
    rng = random.Random(3)
    for _ in range(6):
        inj = garble(clip, len(TEXT), rng)
        ctx = audio_ctx(inj.audio)
        check_audio_signal(ctx, TH, None)
        assert "speech_garbled" in speech_cats(ctx), inj.note


# back-transcription ---------------------------------------------------------------


def test_exact_transcript_has_no_flags():
    ctx = audio_ctx(speechlike(5.0), transcript=TEXT)
    check_transcription(ctx, TH)
    assert speech_cats(ctx) == []
    assert ctx.checks[-1].metrics["wer"] == 0


def test_asr_near_misses_are_noise_not_flags():
    noisy = TEXT.replace("मौसम", "मोसम").replace("पार्क", "पार्क़").replace("खाएंगे", "खायेंगे")
    ctx = audio_ctx(speechlike(5.0), transcript=noisy)
    check_transcription(ctx, TH)
    assert speech_cats(ctx) == []


def test_truncated_transcript_flags_tail_with_text_span():
    words = TEXT.split()
    ctx = audio_ctx(speechlike(5.0), transcript=" ".join(words[:9]))
    check_transcription(ctx, TH)
    sig = next(s for s in ctx.signals if s.category == Category.SPEECH_OMISSION)
    assert "truncated" in sig.message
    assert "खाना" in TEXT[sig.span[0] : sig.span[1]]


def test_repeated_phrase_is_detected():
    words = TEXT.split()
    hyp = " ".join(words[:6] + words[3:6] + words[6:])
    ctx = audio_ctx(speechlike(5.0), transcript=hyp)
    check_transcription(ctx, TH)
    assert "speech_repetition" in speech_cats(ctx)


def test_numbers_spoken_as_words_are_not_omissions():
    text = "मेरे पास 25 किताबें हैं और 3 कलम हैं।"
    ctx = audio_ctx(speechlike(3.0), text=text, transcript="मेरे पास पच्चीस किताबें हैं और तीन कलम हैं")
    check_transcription(ctx, TH)
    assert speech_cats(ctx) == []


# audio injector --------------------------------------------------------------------


def test_audio_injectors_label_text_spans():
    clip = speechlike(6.0)
    chunks = [{"text_start": 0, "text_end": 50, "start_s": 0.0, "end_s": 3.0}, {"text_start": 51, "text_end": len(TEXT), "start_s": 3.0, "end_s": 6.0}]
    rng = random.Random(0)
    t = truncate(clip, len(TEXT), rng, chunks=chunks)
    assert t.audio.duration < clip.duration and t.label["span"] == [51, len(TEXT)]
    r = repeat(clip, len(TEXT), rng, chunks=chunks)
    assert r.audio.duration > clip.duration and r.label["type"] == "speech_repetition"
