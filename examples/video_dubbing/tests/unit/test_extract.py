"""
Unit tests for media audio extraction.
"""

import soundfile as sf

from bodhan_dub.media.extract import extract_audio_for_asr, extract_audio_original
from tests.e2e.test_smoke import generate_synthetic_mp4


def test_audio_extraction(tmp_path):
    video_path = generate_synthetic_mp4(tmp_path / "test_extract.mp4", duration_s=5)
    assert video_path.exists()

    asr_wav = tmp_path / "asr_16k.wav"
    orig_wav = tmp_path / "orig_48k.wav"

    extract_audio_for_asr(video_path, asr_wav)
    assert asr_wav.exists()
    info_asr = sf.info(str(asr_wav))
    assert info_asr.samplerate == 16000
    assert info_asr.channels == 1

    extract_audio_original(video_path, orig_wav, sample_rate=48000)
    assert orig_wav.exists()
    info_orig = sf.info(str(orig_wav))
    assert info_orig.samplerate == 48000
