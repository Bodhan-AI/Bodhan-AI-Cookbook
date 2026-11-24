"""
Unit tests for TTS synthesis, CPS calibrator, and punctuation splitting.
"""

import io

import numpy as np
import pytest
import soundfile as sf

from bodhan_dub.logging import PipelineLogger
from bodhan_dub.stages.tts import CPSCalibrator, TTSSynthesizer, split_text_at_punctuation


def test_cps_calibrator_running_median():
    calibrator = CPSCalibrator()
    # Unseeded voice starts at default 12.0; seeded voice starts at documented API_FACTS CPS
    assert calibrator.get_cps("hi", "UnknownVoice") == 12.0
    initial_cps = calibrator.get_cps("hi", "Kavya")
    assert initial_cps == 11.37

    # Record 3 measurements
    calibrator.record_measurement("hi", "Kavya", 100, 10.0)  # 10.0 cps
    calibrator.record_measurement("hi", "Kavya", 120, 10.0)  # 12.0 cps
    calibrator.record_measurement("hi", "Kavya", 140, 10.0)  # 14.0 cps

    # EWMA/median of history should be around 11.5 - 13.0
    cps = calibrator.get_cps("hi", "Kavya")
    assert 10.0 <= cps <= 14.0

    # Test duration prediction
    pred = calibrator.predict_duration("यह पंद्रह अक्षरों का वाक्य है।", "hi", "Kavya")
    assert pred > 0.5


def test_cps_calibrator_unknown_language_and_voice():
    """B4: Test unknown language or voice starts at default 12.0 and converges via EWMA."""
    calibrator = CPSCalibrator()
    assert calibrator.get_cps("xyz_lang", "UnknownVoice") == 12.0

    # Simulate 5 synthetic segments synthesized at 16.0 cps
    for _ in range(5):
        calibrator.record_measurement("xyz_lang", "UnknownVoice", char_count=80, duration_s=5.0)

    # Must converge towards 16.0 cps
    converged = calibrator.get_cps("xyz_lang", "UnknownVoice")
    assert 14.0 <= converged <= 17.0


def test_split_text_at_punctuation_short_text():
    short = "नमस्ते, आप कैसे हैं?"
    assert split_text_at_punctuation(short, max_chars=180) == [short]


def test_split_text_at_punctuation_long_text():
    part1 = "पहला भाग जो काफी लंबा है और इसमें कई सारे शब्द शामिल हैं।"
    part2 = "दूसरा भाग जो इसके बाद आता है और बात को आगे बढ़ाता है।"
    part3 = "तीसरा भाग जो अंत में आता है और वाक्य को पूर्ण करता है।"
    full = f"{part1} {part2} {part3}"

    pieces = split_text_at_punctuation(full, max_chars=80)
    assert len(pieces) >= 2
    for p in pieces:
        assert len(p) <= 80


@pytest.mark.asyncio
async def test_tts_synthesizer_flow():
    # Create fake audio bytes for speak response (24kHz sine wave)
    t = np.linspace(0, 1.0, 24000, endpoint=False, dtype=np.float32)
    sine_24k = 0.5 * np.sin(2 * np.pi * 440 * t)
    buf = io.BytesIO()
    sf.write(buf, sine_24k, 24000, format="WAV")
    audio_bytes = buf.getvalue()

    class MockBodhanClient:
        async def speak(self, text, voice, language_code, stage, lang, segment_id):
            return audio_bytes

    logger = PipelineLogger(run_id="test_run")
    synthesizer = TTSSynthesizer(
        bodhan_client=MockBodhanClient(),
        logger=logger,
    )

    audio_48k, actual_dur, pred_dur = await synthesizer.synthesize(
        text="नमस्ते दुनिया",
        lang="hi",
        voice="Kavya",
        segment_id="seg_01",
    )

    assert len(audio_48k) > 0
    # Resampled to 48kHz, should be approx 48000 samples (~1 sec)
    assert abs(len(audio_48k) - 48000) < 1000
    assert abs(actual_dur - 1.0) < 0.1


def test_speaker_voices_auto_distinct_pitch():
    """
    C6: When --speaker-voices auto is set, segments with distinct pitch
    are clustered into male/female voices from LanguageRegistry.
    """
    from bodhan_dub.languages import LanguageRegistry
    from bodhan_dub.stages.segment import cluster_speaker_voices, estimate_segment_pitch

    registry = LanguageRegistry()
    sr = 16000

    # Speaker 1: Male low pitch 110 Hz
    t_male = np.linspace(0, 1.0, sr, endpoint=False)
    male_wave = 0.5 * np.sin(2 * np.pi * 110 * t_male)

    # Speaker 2: Female high pitch 220 Hz
    t_female = np.linspace(0, 1.0, sr, endpoint=False)
    female_wave = 0.5 * np.sin(2 * np.pi * 220 * t_female)

    p_male = estimate_segment_pitch(male_wave, sample_rate=sr)
    p_female = estimate_segment_pitch(female_wave, sample_rate=sr)

    # Assert pitch estimations are within 15% of ground truth
    assert abs(p_male - 110.0) < 20.0
    assert abs(p_female - 220.0) < 25.0

    # Cluster voices for Hindi
    voices = cluster_speaker_voices([p_male, p_female], lang="hi", registry=registry)
    assert len(voices) == 2
    # Distinct voices assigned: male voice (Amit) and female voice (Kavya)
    assert voices[0] != voices[1]
    assert voices[0] in ["Amit", "Sourav"]
    assert voices[1] in ["Kavya", "Ishita"]

    # Single speaker with identical pitch gets identical default voice
    voices_single = cluster_speaker_voices([p_female, p_female], lang="hi", registry=registry)
    assert voices_single[0] == voices_single[1]

