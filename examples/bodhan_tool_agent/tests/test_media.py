import numpy as np

from bodhan_mcp import media


def _speechy(sr: int, pattern: list[tuple[float, bool]]) -> np.ndarray:
    parts = []
    rng = np.random.default_rng(0)
    for dur, voiced in pattern:
        n = int(sr * dur)
        parts.append((0.3 * rng.standard_normal(n)).astype(np.float32) if voiced else (0.001 * rng.standard_normal(n)).astype(np.float32))
    return np.concatenate(parts)


def test_segments_never_exceed_model_window():
    sr = 16000
    # 3 minutes of "speech" with a short pause every ~7 s
    x = _speechy(sr, [(7.0, True), (0.4, False)] * 25)
    segs = media.plan_segments(x, sr, max_seconds=28)
    assert segs
    assert all(s.duration <= 29.0 for s in segs)
    covered = sum(s.duration for s in segs)
    assert covered > 0.9 * 7.0 * 25  # nearly all speech is kept


def test_segments_cut_in_pauses():
    sr = 16000
    x = _speechy(sr, [(20.0, True), (1.0, False), (20.0, True)])
    segs = media.plan_segments(x, sr, max_seconds=28)
    assert len(segs) == 2
    assert 19.5 < segs[0].end < 21.5  # the cut lands in the pause, not mid-speech


def test_continuous_speech_is_hard_cut_under_limit():
    sr = 16000
    x = _speechy(sr, [(70.0, True)])
    segs = media.plan_segments(x, sr, max_seconds=28)
    assert len(segs) == 3
    assert all(s.duration <= 29.0 for s in segs)


def test_silence_is_trimmed_and_silent_audio_has_no_segments():
    sr = 16000
    x = _speechy(sr, [(5.0, False), (3.0, True), (5.0, False)])
    segs = media.plan_segments(x, sr)
    assert len(segs) == 1 and 4.5 < segs[0].start < 5.1 and segs[0].duration < 3.6
    assert media.plan_segments(np.zeros(sr * 5, dtype=np.float32), sr) == []


def test_short_subtitle_windows():
    sr = 16000
    x = _speechy(sr, [(4.0, True), (0.3, False)] * 6)
    segs = media.plan_segments(x, sr, max_seconds=8)
    assert all(s.duration <= 9.0 for s in segs)
    assert len(segs) >= 3


def test_concat_and_wav_roundtrip():
    a = media.to_wav_bytes(np.zeros(24000, dtype=np.float32), 24000)
    b = media.to_wav_bytes(np.zeros(12000, dtype=np.float32), 24000)
    joined = media.concat_wavs([a, b], gap_s=0.5)
    assert abs(media.wav_duration(joined) - 2.0) < 0.01


def test_file_kind_sniffs_unknown_extensions(tmp_path):
    p = tmp_path / "noext"
    p.write_bytes(b"%PDF-1.4 rest")
    assert media.file_kind(p) == "pdf"
    q = tmp_path / "notes.weird"
    q.write_text("plain words", encoding="utf-8")
    assert media.file_kind(q) == "text"
