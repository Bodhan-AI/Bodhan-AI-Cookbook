"""Audio windowing: every window ≤ 30 s, contiguous, cut at silences; ffmpeg round trips."""

import math
from array import array

import pytest

from bodhan_core import audio

RATE = 16_000


def synth(pattern: list[tuple[str, float]], rate: int = RATE) -> array:
    """('tone'|'gap', seconds) segments -> PCM16 samples. Tones vary in pitch like speech-ish bursts."""
    out = array("h")
    for i, (kind, secs) in enumerate(pattern):
        n = int(secs * rate)
        if kind == "tone":
            f = 180 + 40 * (i % 5)
            out.extend(int(8000 * math.sin(2 * math.pi * f * t / rate)) for t in range(n))
        else:
            out.extend([0] * n)
    return out


def check_contiguous(windows, total):
    assert windows[0].start_s == 0
    assert windows[-1].end_s == pytest.approx(total, abs=0.03)
    for a, b in zip(windows, windows[1:], strict=False):
        assert b.start_s <= a.end_s  # contiguous or overlapping, never a gap
    assert all(w.duration_s <= audio.MAX_WINDOW_S + 1e-6 for w in windows)


def test_short_audio_is_one_window():
    s = synth([("tone", 6.5)])
    windows = audio.plan_windows(audio.frame_energies(s, RATE))
    assert len(windows) == 1 and windows[0].duration_s == pytest.approx(6.5, abs=0.03)


def test_long_speech_cuts_land_in_silences():
    # 75 s: 4.6 s utterances separated by 0.4 s pauses.
    pattern = []
    for _ in range(15):
        pattern += [("tone", 4.6), ("gap", 0.4)]
    s = synth(pattern)
    windows = audio.plan_windows(audio.frame_energies(s, RATE))
    check_contiguous(windows, 75)
    assert len(windows) == 3
    for w in windows[:-1]:
        in_gap = (w.end_s % 5.0) >= 4.55  # gaps occupy [4.6, 5.0) of every 5 s period
        assert in_gap, f"cut at {w.end_s} is not in a pause"
    assert not any(w.hard_cut_before for w in windows)


def test_continuous_sound_gets_hard_cuts_with_overlap():
    s = synth([("tone", 70.0)])
    windows = audio.plan_windows(audio.frame_energies(s, RATE))
    check_contiguous(windows, 70)
    assert any(w.hard_cut_before for w in windows[1:])
    assert windows[1].start_s == pytest.approx(windows[0].end_s - audio.OVERLAP_S, abs=0.01)


def test_silent_windows_are_marked_for_skipping():
    s = synth([("tone", 5), ("gap", 60), ("tone", 5)])
    windows = audio.plan_windows(audio.frame_energies(s, RATE))
    check_contiguous(windows, 70)
    assert any(w.silent for w in windows)
    assert not windows[0].silent and not windows[-1].silent


def test_subtitle_sized_windows():
    pattern = [("tone", 2.0), ("gap", 0.3)] * 12
    s = synth(pattern)
    windows = audio.plan_windows(audio.frame_energies(s, RATE), max_s=8.0, min_s=2.0)
    assert all(w.duration_s <= 8.0 for w in windows) and len(windows) >= 4


def test_concat_and_slice(tmp_path):
    a = audio.wav_bytes(synth([("tone", 1.0)], 24_000), 24_000)
    b = audio.wav_bytes(synth([("tone", 2.0)], 24_000), 24_000)
    joined = audio.concat_wavs([a, b], gap_ms=500)
    assert audio.wav_duration(joined) == pytest.approx(3.5, abs=0.01)
    samples, rate = audio.read_pcm(joined)
    assert audio.wav_duration(audio.slice_wav(samples, rate, 1.0, 2.0)) == pytest.approx(1.0, abs=0.01)


def test_ffmpeg_decode_probe_and_mp3(tmp_path):
    src = tmp_path / "in.wav"
    src.write_bytes(audio.wav_bytes(synth([("tone", 2.0)], 44_100), 44_100))
    wav = audio.to_wav16k(src, tmp_path / "out.wav")
    samples, rate = audio.read_pcm(wav)
    assert rate == 16_000 and len(samples) == pytest.approx(32_000, abs=200)
    mp3 = audio.wav_to_mp3(wav, tmp_path / "out.mp3", title="t", comment="AI-generated")
    info = audio.probe(mp3)
    assert info["audio_codec"] == "mp3" and info["duration_s"] == pytest.approx(2.0, abs=0.1) and not info["has_video"]


def test_bad_media_is_structured_error(tmp_path):
    bad = tmp_path / "x.wav"
    bad.write_bytes(b"not audio")
    with pytest.raises(audio.ToolError) as exc:
        audio.probe(bad)
    assert exc.value.info.code.value == "UNSUPPORTED_MEDIA"
