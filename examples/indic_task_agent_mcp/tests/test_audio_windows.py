"""Chunking of long audio into model-sized windows."""

import numpy as np
from conftest import silence, tone

from bodhan_mcp.audio import MAX_WINDOW_S, plan_windows, speech_mask


def test_windows_never_exceed_limit_and_cut_in_silence():
    parts = []
    for i in range(12):  # 12 utterances of 5-9 s separated by 0.6 s pauses, about 90 s in all
        parts += [tone(5 + (i % 5)), silence(0.6)]
    audio = np.concatenate(parts)
    windows = plan_windows(audio)
    mask = speech_mask(audio)
    assert all(w.end_s - w.start_s <= MAX_WINDOW_S + 1e-6 for w in windows)
    assert not any(w.hard_cut for w in windows)
    for w in windows[:-1]:
        frame = int(w.end_s / 0.03)
        assert not mask[min(frame, len(mask) - 1)], f"cut at {w.end_s}s lands in speech"
    assert abs(windows[-1].end_s - len(audio) / 16000) < 0.05
    covered = sum(w.end_s - w.start_s for w in windows)
    assert abs(covered - len(audio) / 16000) < 0.05


def test_unbroken_speech_gets_hard_cut_with_overlap():
    audio = tone(65)
    windows = plan_windows(audio)
    assert all(w.end_s - w.start_s <= MAX_WINDOW_S + 1e-6 for w in windows)
    assert windows[0].hard_cut
    assert windows[1].start_s < windows[0].end_s  # overlap so no word is lost


def test_silence_is_never_sent():
    audio = np.concatenate([silence(40), tone(3), silence(40)])
    windows = plan_windows(audio)
    speech = [w for w in windows if w.has_speech]
    assert len(speech) == 1
    assert speech[0].speech_start_s >= 39.5


def test_short_audio_is_one_window():
    windows = plan_windows(tone(4))
    assert len(windows) == 1 and windows[0].has_speech
