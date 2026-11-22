import pytest

from bodhan_mcp.audio_plan import (
    API_MAX_SECONDS, extract_window_cmd, parse_silencedetect, plan_windows, silencedetect_cmd,
)

SAMPLE_LOG = """
[silencedetect @ 0x55] silence_start: -0.00133
[silencedetect @ 0x55] silence_end: 1.5 | silence_duration: 1.50133
[silencedetect @ 0x55] silence_start: 12.25
[silencedetect @ 0x55] silence_end: 13.75 | silence_duration: 1.5
[silencedetect @ 0x55] silence_start: 58.0
"""


def test_parse_silencedetect():
    assert parse_silencedetect(SAMPLE_LOG) == [(0.0, 1.5), (12.25, 13.75)]
    # an unfinished silence at the end is closed with the total duration
    assert parse_silencedetect(SAMPLE_LOG, total_duration=60.0)[-1] == (58.0, 60.0)


def _assert_valid(windows, duration):
    assert windows[0][0] == 0
    assert windows[-1][1] == duration
    for (s1, e1), (s2, e2) in zip(windows, windows[1:]):
        assert e1 == s2  # contiguous
    assert all(0 < e - s <= API_MAX_SECONDS for s, e in windows)


def test_short_audio_is_one_window():
    assert plan_windows(12.0) == [(0.0, 12.0)]


def test_long_audio_without_silence():
    windows = plan_windows(60.0)
    assert windows == [(0.0, 25.0), (25.0, 50.0), (50.0, 60.0)]
    _assert_valid(windows, 60.0)


def test_cuts_land_on_silence():
    windows = plan_windows(40.0, silences=[(20.0, 21.0), (24.0, 25.0)])
    assert windows[0][1] == 24.5  # the latest silence inside the 25 s limit
    _assert_valid(windows, 40.0)


def test_target_len_gives_shorter_windows():
    windows = plan_windows(40.0, silences=[(7.5, 8.5), (20.0, 21.0), (30.0, 31.0)], target_len=8.0)
    assert windows[0][1] == 8.0
    _assert_valid(windows, 40.0)


def test_tiny_tail_is_merged():
    # 26 s would leave a 1 s tail after a 25 s window: merged into one 26 s window (< 30 s cap)
    assert plan_windows(26.0) == [(0.0, 26.0)]


def test_subtitle_mode_keeps_cutting_the_tail():
    windows = plan_windows(40.0, target_len=8.0)
    _assert_valid(windows, 40.0)
    assert all(e - s <= 12.5 for s, e in windows)


def test_zero_and_bad_arguments():
    assert plan_windows(0) == []
    with pytest.raises(ValueError):
        plan_windows(10, max_len=31)
    with pytest.raises(ValueError):
        plan_windows(10, min_len=5, max_len=4)


def test_command_builders_are_lists_of_strings():
    cmd = silencedetect_cmd("a.mp4")
    assert cmd[0] == "ffmpeg" and "silencedetect=noise=-35dB:d=0.4" in cmd
    cut = extract_window_cmd("a.mp4", "w.wav", 1.0, 3.5)
    assert cut[0] == "ffmpeg" and "2.500" in cut and "16000" in cut
    assert all(isinstance(part, str) for part in cut)
