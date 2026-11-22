"""Segmentation logic: chunks stay short, cut only at pauses, and cover all speech exactly once."""

import random

import pytest

from bodhan_dub.chunking import ChunkParams, pack_chunks
from bodhan_dub.vad import Region

P = ChunkParams(max_len_s=15.0, min_len_s=3.0, sentence_pause_s=0.8)


def spans(chunks):
    return [(c.start, c.end, c.forced_cut) for c in chunks]


def test_empty_input_gives_no_chunks():
    assert pack_chunks([], P) == []


def test_single_short_region_is_one_chunk():
    assert spans(pack_chunks([Region(2.0, 6.5)], P)) == [(2.0, 6.5, False)]


def test_sentence_pause_ends_a_chunk_once_it_is_long_enough():
    regions = [Region(0, 4), Region(5, 9)]  # 1.0 s pause after a 4 s chunk
    assert spans(pack_chunks(regions, P)) == [(0, 4, False), (5, 9, False)]


def test_sentence_pause_is_ignored_while_chunk_is_too_short():
    regions = [Region(0, 1), Region(2, 5)]  # 1.0 s pause but the head is only 1 s
    assert spans(pack_chunks(regions, P)) == [(0, 5, False)]


def test_short_pauses_are_kept_inside_a_chunk():
    regions = [Region(0, 3), Region(3.4, 6), Region(6.5, 9)]
    assert spans(pack_chunks(regions, P)) == [(0, 9, False)]


def test_overflow_cuts_at_the_longest_pause_not_the_last_one():
    # Pauses: 0.3 s, 0.7 s, 0.3 s. The whole run is 16 s > 15 s, so one cut is needed,
    # and it should land in the 0.7 s pause (8 -> 8.7), the likeliest sentence end.
    regions = [Region(0, 4), Region(4.3, 8), Region(8.7, 12), Region(12.3, 16)]
    assert spans(pack_chunks(regions, P)) == [(0, 8, False), (8.7, 16, False)]


def test_nonstop_speech_is_force_split_at_the_cut_finder_point():
    chunks = pack_chunks([Region(0, 40)], P, find_cut=lambda lo, hi: hi - 1.0)
    assert all(c.forced_cut for c in chunks)
    assert all(c.duration <= P.max_len_s + 1e-9 for c in chunks)
    assert chunks[0].start == 0 and chunks[-1].end == 40
    assert [c.start for c in chunks[1:]] == [c.end for c in chunks[:-1]]  # contiguous, nothing lost
    assert chunks[0].end == pytest.approx(14.0)


def test_forced_split_never_leaves_a_tiny_tail():
    chunks = pack_chunks([Region(0, 16)], P)  # midpoint cut: 8 + 8, not 15 + 1
    assert [round(c.duration, 3) for c in chunks] == [8.0, 8.0]


def random_regions(rng: random.Random) -> list[Region]:
    t, out = rng.uniform(0, 3), []
    for _ in range(rng.randint(1, 40)):
        length = rng.choice([rng.uniform(0.3, 4), rng.uniform(4, 25)])
        out.append(Region(round(t, 3), round(t + length, 3)))
        t += length + rng.choice([rng.uniform(0.3, 0.7), rng.uniform(0.8, 3)])
    return out


@pytest.mark.parametrize("seed", range(200))
def test_invariants_hold_on_random_layouts(seed):
    regions = random_regions(random.Random(seed))
    chunks = pack_chunks(regions, P)
    starts = {r.start for r in regions}
    ends = {r.end for r in regions}

    for c in chunks:
        assert 0 < c.duration <= P.max_len_s + 1e-9
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert a.end <= b.start  # ordered, never overlapping
    # Unforced chunk edges sit exactly on region edges, i.e. inside pauses.
    for c in chunks:
        if not c.forced_cut:
            assert c.start in starts and c.end in ends
    # Every second of speech lands in exactly one chunk.
    speech = sum(r.duration for r in regions)
    covered = sum(min(r.end, c.end) - max(r.start, c.start) for r in regions for c in chunks
                  if min(r.end, c.end) > max(r.start, c.start))
    assert covered == pytest.approx(speech, abs=1e-6)
    assert chunks == pack_chunks(regions, P)  # deterministic
