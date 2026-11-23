"""Segmentation geometry: windows stay <=30s, cut at silence, never split sentences.

These operate on synthetic boolean speech masks so the windowing logic is tested
independently of any VAD backend.
"""

import numpy as np

from bodhan_dub.pipeline.segment import (
    MAX_WINDOW_S,
    cap_window,
    non_speech_spans,
    spans_from_mask,
)

FS = 0.030  # 30 ms frames


def test_merges_small_gaps_into_one_span():
    # two speech runs separated by one 30 ms frame (< 0.4 s merge gap) -> one span
    mask = np.array([True] * 10 + [False] * 1 + [True] * 10)
    spans = spans_from_mask(mask, min_speech_s=0.0, merge_gap_s=0.4)
    assert len(spans) == 1
    assert spans[0][0] == 0.0


def test_splits_on_large_silence():
    # a 1.2 s silence (40 frames) exceeds the merge gap -> two separate segments
    mask = np.array([True] * 10 + [False] * 40 + [True] * 10)
    spans = spans_from_mask(mask, min_speech_s=0.0)
    assert len(spans) == 2


def test_drops_subminimum_blips():
    # a 60 ms blip (2 frames) is below the 0.3 s floor and is discarded
    mask = np.array([True] * 2 + [False] * 30 + [True] * 20)
    spans = spans_from_mask(mask)
    assert len(spans) == 1
    assert spans[0][0] > 0.5


def test_cap_window_never_exceeds_max_and_is_contiguous():
    out = cap_window((0.0, 100.0), max_window_s=MAX_WINDOW_S)
    assert all((e - s) <= MAX_WINDOW_S + 1e-6 for s, e in out)
    assert out[0][0] == 0.0 and abs(out[-1][1] - 100.0) < 1e-6
    for a, b in zip(out, out[1:], strict=False):
        assert abs(a[1] - b[0]) < 1e-6  # no gaps, no overlaps


def test_short_span_passes_through_unchanged():
    assert cap_window((1.0, 5.0)) == [(1.0, 5.0)]


def test_cap_window_prefers_low_energy_cut():
    # build energy that is quiet near 20 s so the split lands there, not at 29 s
    frames = int(60 / FS)
    energy = np.ones(frames)
    quiet = int(20 / FS)
    energy[quiet - 2 : quiet + 2] = 0.0
    out = cap_window((0.0, 60.0), max_window_s=29.0, energy=energy, frame_ms=30)
    first_cut = out[0][1]
    assert 17.0 < first_cut < 21.0  # snapped to the quiet region, not the hard cap


def test_non_speech_is_exact_complement():
    gaps = non_speech_spans([(1.0, 2.0), (3.0, 4.0)], total=5.0)
    assert gaps == [(0.0, 1.0), (2.0, 3.0), (4.0, 5.0)]
