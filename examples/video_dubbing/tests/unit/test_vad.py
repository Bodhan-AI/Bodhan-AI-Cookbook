"""
Unit tests for Voice Activity Detection and pause inversion.
"""

import numpy as np
import soundfile as sf

from bodhan_dub.media.vad import Pause, SileroVADDetector, invert_pauses_to_speech


def test_invert_pauses_to_speech():
    total_dur = 10.0
    pauses = [
        Pause(start=2.0, end=4.0),
        Pause(start=7.0, end=8.5),
    ]

    speech = invert_pauses_to_speech(pauses, total_dur)
    assert len(speech) == 3

    assert speech[0].start == 0.0
    assert speech[0].end == 2.0

    assert speech[1].start == 4.0
    assert speech[1].end == 7.0

    assert speech[2].start == 8.5
    assert speech[2].end == 10.0


def test_empty_pauses_creates_full_speech():
    speech = invert_pauses_to_speech([], 12.0)
    assert len(speech) == 1
    assert speech[0].start == 0.0
    assert speech[0].end == 12.0


def test_silero_vad_missing_weights_uses_ffmpeg(tmp_path):
    """C5: Assert missing weights causes clean fallback to ffmpeg with no crash."""
    detector = SileroVADDetector(model_path=tmp_path / "non_existent.onnx")
    assert detector._session is None

    # Create dummy 16kHz audio
    dummy_wav = tmp_path / "test_16k.wav"
    audio = np.zeros(16000 * 2, dtype=np.float32)
    # 0.5s tone in middle
    audio[8000:16000] = 0.5 * np.sin(2 * np.pi * 440 * np.linspace(0, 0.5, 8000))
    sf.write(str(dummy_wav), audio, 16000)

    res = detector.process(dummy_wav)
    assert res.engine_used == "ffmpeg_silencedetect"
    assert res.total_duration >= 1.9


def test_silero_vad_corrupt_weights_fails_hash_check_and_falls_back(tmp_path):
    """C5: Assert corrupt weights fails SHA256 check and falls back cleanly without crash."""
    corrupt_file = tmp_path / "corrupt_silero.onnx"
    corrupt_file.write_bytes(b"CORRUPTED_ONNX_MODEL_BYTES_WITH_WRONG_HASH")

    detector = SileroVADDetector(model_path=corrupt_file)
    assert detector._session is None

    # Create dummy 16kHz audio
    dummy_wav = tmp_path / "test_16k_corrupt.wav"
    audio = np.zeros(16000 * 2, dtype=np.float32)
    sf.write(str(dummy_wav), audio, 16000)

    res = detector.process(dummy_wav)
    assert res.engine_used == "ffmpeg_silencedetect"
