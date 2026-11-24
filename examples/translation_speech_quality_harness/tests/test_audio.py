import wave

import numpy as np
import pytest
from bodhan_eval.audio import (
    FfmpegMissing,
    chunk_at_silence,
    clipping_ratio,
    concat_wavs,
    silence_stats,
    to_wav,
    voiced_regions,
)


def write_tone(path, seconds=95.0, sr=24000):
    total = int(seconds * sr)
    samples = np.zeros(total, dtype=np.float32)
    first_end = min(30 * sr, total)
    first_length = max(0, first_end - 1 * sr)
    samples[1 * sr:first_end] = 0.2 * np.sin(
        2 * np.pi * 440 * np.arange(first_length) / sr
    )
    second_start = min(35 * sr, total)
    second_length = max(0, total - second_start)
    samples[second_start:total] = 0.2 * np.sin(
        2 * np.pi * 440 * np.arange(second_length) / sr
    )
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def test_95_second_chunks_never_exceed_28(tmp_path):
    path = tmp_path / "long.wav"
    write_tone(path)
    wav = to_wav(path)
    chunks = chunk_at_silence(wav, 28.0)
    assert chunks
    assert all(chunk.end_s - chunk.start_s <= 28.0001 for chunk in chunks)
    assert abs(chunks[-1].end_s - 95.0) < 0.001


def test_silence_clipping_and_voiced_regions(tmp_path):
    path = tmp_path / "audio.wav"
    write_tone(path, seconds=10)
    wav = to_wav(path)
    stats = silence_stats(wav)
    assert stats["ratio"] > 0.0
    assert stats["longest_s"] >= 1.0
    assert clipping_ratio(wav) == 0.0
    assert voiced_regions(wav)


def test_concat_wavs(tmp_path):
    path = tmp_path / "a.wav"
    write_tone(path, seconds=2)
    wav = to_wav(path)
    joined = concat_wavs([wav, wav])
    assert abs(joined.duration_s - 4.0) < 0.001


def test_missing_ffmpeg_error(monkeypatch):
    import bodhan_eval.audio as audio

    monkeypatch.setattr(audio.shutil, "which", lambda _: None)
    with pytest.raises(FfmpegMissing):
        to_wav(b"RIFF", ffmpeg=True)
