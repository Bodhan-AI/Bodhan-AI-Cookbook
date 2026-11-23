from __future__ import annotations

import numpy as np
import pytest

from bodhan_dub import chunker
from bodhan_dub.vad import VadParams
from tests.conftest import SR, build_signal, burst, silence


def test_no_speech_gives_no_chunks() -> None:
    assert chunker.plan(silence(3.0), SR) == []


def test_one_utterance_stays_one_chunk() -> None:
    samples = build_signal([(1.0, 3.0)], total_s=5.0)
    chunks = chunker.plan(samples, SR, vad_params=VadParams(tail_pad_s=0.0))

    assert len(chunks) == 1
    assert chunks[0].start_s == pytest.approx(0.9, abs=0.1)
    assert chunks[0].end_s == pytest.approx(3.0, abs=0.1)
    assert chunks[0].split is False


def test_no_chunk_exceeds_the_cap() -> None:
    samples = burst(70.0, amplitude=0.3)
    chunks = chunker.plan(samples, SR, max_chunk_s=28.0)

    assert len(chunks) >= 3
    for chunk in chunks:
        assert chunk.duration_s <= 28.0 + 1e-6


def test_split_covers_the_whole_region_without_gaps() -> None:
    samples = burst(70.0, amplitude=0.3)
    chunks = chunker.plan(samples, SR, max_chunk_s=28.0, vad_params=VadParams(pad_s=0.0))

    assert chunks[0].start_s == pytest.approx(0.0, abs=0.05)
    assert chunks[-1].end_s == pytest.approx(70.0, abs=0.05)
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        assert earlier.end_s == pytest.approx(later.start_s, abs=1e-6)


def test_cut_lands_on_a_quiet_dip() -> None:
    # Continuous speech with 100 ms quiet notches every 5 s.
    samples = burst(60.0, amplitude=0.3)
    notches = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
    for centre in notches:
        first = int((centre - 0.05) * SR)
        last = int((centre + 0.05) * SR)
        samples[first:last] *= 0.01

    chunks = chunker.plan(samples, SR, max_chunk_s=28.0, vad_params=VadParams(pad_s=0.0))

    first_cut = chunks[0].end_s
    assert min(abs(first_cut - centre) for centre in notches) < 0.25


def test_chunk_sample_indices_match_the_times() -> None:
    samples = build_signal([(1.0, 4.0)], total_s=6.0)
    chunk = chunker.plan(samples, SR)[0]

    assert chunk.start_sample == round(chunk.start_s * SR)
    assert chunk.end_sample == round(chunk.end_s * SR)
    assert chunk.end_sample > chunk.start_sample


def test_short_tail_is_folded_into_the_previous_chunk() -> None:
    samples = burst(56.5, amplitude=0.3)
    chunks = chunker.plan(samples, SR, max_chunk_s=28.0, vad_params=VadParams(pad_s=0.0))

    assert all(chunk.duration_s > 0.7 for chunk in chunks)
    assert chunks[-1].end_s == pytest.approx(56.5, abs=0.05)


def test_chunk_indices_are_sequential() -> None:
    samples = burst(60.0, amplitude=0.3)
    chunks = chunker.plan(samples, SR, max_chunk_s=28.0)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_two_speakers_give_two_chunks() -> None:
    samples = build_signal([(0.5, 2.0), (3.0, 4.5)], total_s=5.5)
    chunks = chunker.plan(samples, SR, vad_params=VadParams(pad_s=0.0))

    assert len(chunks) == 2
    assert np.all(np.diff([chunk.start_s for chunk in chunks]) > 0)
