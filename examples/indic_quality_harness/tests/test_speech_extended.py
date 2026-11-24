import math
import struct
import wave

from bodhan_eval.audio_checks import evaluate_audio_quality, evaluate_duration_vs_text, inspect_audio
from bodhan_eval.speech_checks import evaluate_speech, evaluate_speech_coverage
from bodhan_eval.speech_service import speech_cache_key

INTENDED = "बैठक सुबह 10 बजे शुरू होगी"


def statuses(checks):
    return {c["category"]: c["status"] for c in checks}


def test_complete_transcript_passes_coverage():
    assert statuses(evaluate_speech_coverage(INTENDED, INTENDED, "hi")) == {
        "speech_missing_content": "pass", "speech_truncation": "pass",
    }


def test_truncated_transcript_fails():
    result = statuses(evaluate_speech_coverage(INTENDED, "बैठक सुबह 10", "hi"))
    assert result == {"speech_missing_content": "fail", "speech_truncation": "fail"}


def test_missing_middle_word_is_review_not_truncation():
    result = statuses(evaluate_speech_coverage(INTENDED, "बैठक सुबह 10 बजे होगी", "hi"))
    assert result["speech_missing_content"] == "review"
    assert result["speech_truncation"] == "pass"


def test_empty_transcript_fails_everything():
    result = statuses(evaluate_speech_coverage(INTENDED, "", "hi"))
    assert set(result.values()) == {"fail"}


def test_spelled_number_counts_as_spoken():
    result = evaluate_speech_coverage("कल सुबह 10 बजे", "कल सुबह दस बजे", "hi")
    assert statuses(result)["speech_missing_content"] == "pass"


def test_intended_repetition_is_not_a_speech_error():
    result = evaluate_speech("बहुत बहुत धन्यवाद", "बहुत बहुत धन्यवाद", "hi")
    assert result["status"] == "pass"


def test_bigram_repetition_detected():
    result = evaluate_speech("आज मौसम अच्छा है", "आज मौसम आज मौसम अच्छा है", "hi")
    assert result["status"] == "fail"
    assert "आज मौसम" in result["evidence"]["repetitions"]


def write_wav(path, seconds=1.0, rate=16000, silent=False, amplitude=0.5):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", 0 if silent else int(32767 * amplitude * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(int(seconds * rate))
        )
        wav.writeframes(frames)


def test_frame_silence_on_tone_and_silence(tmp_path):
    tone = tmp_path / "tone.wav"
    silence = tmp_path / "silence.wav"
    write_wav(tone)
    write_wav(silence, silent=True)

    tone_metrics = inspect_audio(str(tone))
    silence_metrics = inspect_audio(str(silence))

    assert tone_metrics["frame_silence_ratio"] == 0
    assert silence_metrics["frame_silence_ratio"] == 1
    assert silence_metrics["leading_silence_seconds"] == 1.0

    silence_check = next(c for c in evaluate_audio_quality(silence_metrics) if c["category"] == "audio_silence")
    assert silence_check["status"] == "review"


def test_clipping_detected(tmp_path):
    clipped = tmp_path / "clipped.wav"
    # A full-scale sine spends ~9 % of its samples above 99 % of full scale.
    write_wav(clipped, amplitude=1.0)
    metrics = inspect_audio(str(clipped))
    assert metrics["clipping_ratio"] > 0.01


def test_duration_vs_text():
    assert evaluate_duration_vs_text(2.5, 5)["status"] == "pass"
    assert evaluate_duration_vs_text(0.3, 10)["status"] == "review"
    assert evaluate_duration_vs_text(30.0, 5)["status"] == "review"
    assert evaluate_duration_vs_text(1.0, 0) is None


def test_speech_cache_key_covers_all_inputs():
    base = speech_cache_key("नमस्ते", "hi", "Amit")
    assert base == speech_cache_key("नमस्ते", "hi", "Amit")
    assert base != speech_cache_key("नमस्ते!", "hi", "Amit")
    assert base != speech_cache_key("नमस्ते", "bn", "Amit")
    assert base != speech_cache_key("नमस्ते", "hi", "Kavya")
