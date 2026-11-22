import numpy as np
import soundfile as sf

from bodhan_dub.assemble import build_speech_track, first_voiced_time, merge_intervals, mute_envelope
from bodhan_dub.subtitles import srt_time, write_srt


def test_build_speech_track_places_clips(tmp_path):
    sr = 24000
    p = tmp_path / "c.wav"
    sf.write(p, np.full(sr, 0.5, dtype="float32"), sr)  # 1 s
    t = build_speech_track([{"clip_path": str(p), "placed_start": 2.0}, {"id": 9}], 5.0)
    assert len(t) == 5 * sr
    assert t[: 2 * sr].max() == 0 and abs(t[2 * sr + 10] - 0.5) < 1e-3 and t[3 * sr + 10] == 0


def test_build_speech_track_trims_at_end(tmp_path):
    sr = 24000
    p = tmp_path / "c.wav"
    sf.write(p, np.full(2 * sr, 0.5, dtype="float32"), sr)
    assert len(build_speech_track([{"clip_path": str(p), "placed_start": 4.5}], 5.0)) == 5 * sr


def test_merge_intervals():
    assert merge_intervals([(5, 6), (0, 2), (1.5, 3)]) == [(0, 3), (5, 6)]


def test_mute_envelope_mutes_and_fades():
    sr = 1000
    env = mute_envelope(10 * sr, sr, [(2.0, 4.0)], fade=0.04)
    assert env[500] == 1.0 and env[9000] == 1.0       # untouched
    assert env[3000] == 0.0                           # muted
    assert 0.0 < env[2000] < 1.0                      # ramp at the edge
    assert np.all(np.diff(env[1950:2050]) <= 1e-6)    # monotonic down


def test_first_voiced_time(tmp_path):
    sr = 24000
    w = np.zeros(sr, dtype="float32")
    w[sr // 2 :] = 0.4
    p = tmp_path / "a.wav"
    sf.write(p, w, sr)
    assert abs(first_voiced_time(p) - 0.5) < 0.01


def test_srt(tmp_path):
    assert srt_time(3661.5) == "01:01:01,500"
    f = write_srt([(0.0, 1.5, "नमस्ते"), (2.0, 2.0, "  "), (3.0, 4.0, "ठीक है")], tmp_path / "a.srt")
    txt = f.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:01,500\nनमस्ते" in txt and "2\n00:00:03,000" in txt
