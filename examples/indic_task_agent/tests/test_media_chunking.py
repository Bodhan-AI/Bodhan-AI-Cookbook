"""Audio chunking honours the 30 s model limit and cuts at pauses."""

import numpy as np
from conftest import ffmpeg, speechlike, write_wav

from bodhan_mcp.media import chunk_audio, decode_pcm, probe


def test_chunks_never_exceed_28s_and_cut_in_pauses():
    rate = 16000
    x = speechlike(95, rate, pause_every=6.0)
    chunks = chunk_audio(x, rate, max_seconds=28.0)
    assert len(chunks) >= 4
    assert all(c.duration <= 28.0 + 1e-6 for c in chunks)
    # every chunk boundary lies in (or next to) a silent gap, never mid-burst
    for c in chunks[:-1]:
        tail = x[int(c.end * rate) - 160 : int(c.end * rate)]
        assert np.abs(tail).mean() < 0.05
    # covered speech: nothing voiced is lost
    covered = sum(c.duration for c in chunks)
    assert covered > 95 * 0.85


def test_silence_only_gives_no_chunks():
    assert chunk_audio(np.zeros(16000 * 10, dtype=np.float32), 16000) == []


def test_short_audio_is_one_trimmed_chunk():
    rate = 16000
    x = np.concatenate([np.zeros(rate * 2), speechlike(4, rate, pause_every=10), np.zeros(rate * 3)]).astype(np.float32)
    chunks = chunk_audio(x, rate)
    assert len(chunks) == 1
    assert chunks[0].start > 1.5 and chunks[0].duration < 5.0  # leading/trailing silence not billed


def test_max_segment_respected_for_subtitle_cues():
    x = speechlike(40, 16000, pause_every=3.0)
    chunks = chunk_audio(x, 16000, max_seconds=7.0, min_seconds=1.5)
    assert all(c.duration <= 7.0 + 1e-6 for c in chunks)


def test_decode_any_container(tmp_path):
    wav = write_wav(tmp_path / "a.wav", speechlike(5, 16000))
    mp3 = tmp_path / "a.mp3"
    ffmpeg("-i", str(wav), str(mp3))
    mp4 = tmp_path / "v.mp4"
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=160x120:d=5", "-i", str(wav), "-shortest", "-c:v", "libx264", "-c:a", "aac", str(mp4))
    for f in (wav, mp3, mp4):
        x = decode_pcm(f)
        assert 4.5 < len(x) / 16000 < 5.5
    info = probe(mp4)
    assert info.has_audio and info.has_video
