import numpy as np
import soundfile as sf

from pathlib import Path

from bodhan_eval.speech_checks import SpeechQualityChecker


def main():
    checker = SpeechQualityChecker()

    test_dir = Path("outputs/test_audio")
    test_dir.mkdir(parents=True, exist_ok=True)

    audio_path = test_dir / "sample.wav"

    # Generate 2 seconds of test audio.
    sample_rate = 24000
    duration = 2

    time = np.linspace(
        0,
        duration,
        sample_rate * duration,
        endpoint=False
    )

    # A 440 Hz sine wave for testing.
    audio = 0.5 * np.sin(2 * np.pi * 440 * time)

    sf.write(audio_path, audio, sample_rate)

    print("=" * 50)
    print("INDICEVAL - SPEECH QUALITY TEST")
    print("=" * 50)

    # Test 1: Audio quality
    print("\n[TEST 1] Audio analysis")

    audio_result = checker.analyze_audio(audio_path)

    print(audio_result)

    assert audio_result["status"] == "passed"
    assert audio_result["duration_seconds"] == 2.0

    # Test 2: Correct transcription
    print("\n[TEST 2] Correct transcription")

    result = checker.compare_transcription(
        expected_text="नमस्ते आप कैसे हैं",
        actual_text="नमस्ते आप कैसे हैं"
    )

    print(result)

    assert result["status"] == "passed"
    assert result["similarity"] == 1.0

    # Test 3: Repeated words
    print("\n[TEST 3] Repetition detection")

    result = checker.compare_transcription(
        expected_text="Hello how are you",
        actual_text="Hello hello how are you"
    )

    print(result)

    assert "hello" in result["repetitions"]

    # Test 4: Missing audio
    print("\n[TEST 4] Missing audio")

    result = checker.analyze_audio(
        "outputs/test_audio/missing.wav"
    )

    print(result)

    assert result["status"] == "failed"

    print("\n" + "=" * 50)
    print("ALL SPEECH TESTS PASSED!")
    print("=" * 50)


if __name__ == "__main__":
    main()