import numpy as np

from bodhan_mcp import audio as au

SR = 16000


def test_short_audio_is_one_window():
    x = np.ones(SR * 10, dtype="float32") * 0.1
    assert au.split_for_stt(x, SR) == [(0, len(x))]


def test_long_audio_split_into_windows_under_limit_covering_everything():
    t = np.arange(SR * 100) / SR
    x = (0.3 * np.sin(2 * np.pi * 200 * t)).astype("float32")
    spans = au.split_for_stt(x, SR)
    assert spans[0][0] == 0 and spans[-1][1] == len(x)
    assert all(b - a <= 28 * SR for a, b in spans)
    assert all(spans[i][1] == spans[i + 1][0] for i in range(len(spans) - 1))  # no gaps, no overlap


def test_cuts_land_in_pauses():
    x = 0.3 * np.sin(2 * np.pi * 200 * (np.arange(SR * 60) / SR)).astype("float32")
    pause = slice(int(24.0 * SR), int(24.4 * SR))   # a 400 ms pause inside the search region of the first window
    x[pause] = 0
    first_cut = au.split_for_stt(x, SR)[0][1]
    assert pause.start - 0.1 * SR <= first_cut <= pause.stop + 0.1 * SR


def test_silence_detection_and_stats():
    assert au.is_silent(np.zeros(SR, dtype="float32"))
    assert not au.is_silent(0.2 * np.ones(SR, dtype="float32"))
    assert au.stats(np.ones(SR, dtype="float32"), SR)["clipping"]


def test_join_inserts_gaps():
    a = np.ones(100, dtype="float32")
    out, sr = au.join([(a, 1000), (a, 1000)], gap_s=0.5)
    assert len(out) == 100 + 500 + 100
