from bodhan_dub.chunking import ChunkParams, build_chunks, merge_regions, split_group

P = ChunkParams()


def test_merge_regions_joins_small_gaps():
    assert merge_regions([(0, 1), (1.1, 2), (3, 4)], 0.2) == [(0, 2), (3, 4)]


def test_merge_regions_sorts_and_ignores_empty():
    assert merge_regions([(3, 4), (0, 1), (5, 5)], 0.2) == [(0, 1), (3, 4)]


def test_empty_input():
    assert build_chunks([], 10.0) == []


def test_cuts_at_long_pause():
    chunks = build_chunks([(0, 3), (4, 7)], 10.0)  # 1.0 s pause >= cut_gap
    assert len(chunks) == 2


def test_short_pause_stays_in_one_chunk():
    chunks = build_chunks([(0, 3), (3.4, 7)], 10.0)  # 0.4 s pause < cut_gap
    assert len(chunks) == 1
    assert chunks[0].start == 0.0 and abs(chunks[0].end - 7.15) < 1e-6


def test_never_exceeds_max_len():
    # one hour of speech with 0.4 s pauses (never >= cut_gap), 5 s phrases
    regions = [(i * 5.4, i * 5.4 + 5.0) for i in range(60)]
    chunks = build_chunks(regions, 400.0)
    assert len(chunks) > 1
    assert all(c.duration <= P.max_len + 1e-6 for c in chunks)


def test_long_group_cuts_at_longest_gap():
    # budget ~27.7: regions 0-10, 10.3-20, 20.5-30(gap .5) ... cut should use the biggest gap that fits
    subs = [(0, 10), (10.3, 20), (20.5, 26), (26.2, 40)]
    pieces = split_group(subs, 27.7)
    assert pieces[0][1] in (20, 26)  # a gap boundary, not mid-region
    assert all(e - s <= 27.7 + 1e-6 for s, e, _ in pieces)


def test_single_long_region_is_hard_cut():
    pieces = split_group([(0, 70)], 27.7)
    assert len(pieces) == 3
    assert all(e - s <= 27.7 + 1e-6 for s, e, _ in pieces)
    assert pieces[0][1] == pieces[1][0]  # contiguous, nothing lost


def test_hard_cut_uses_energy_fn():
    pieces = split_group([(0, 40)], 27.7, energy_fn=lambda lo, hi: 24.0)
    assert pieces[0][1] == 24.0


def test_drops_tiny_speech():
    chunks = build_chunks([(0, 0.1), (5, 8)], 10.0)
    assert len(chunks) == 1 and chunks[0].start > 4


def test_padding_clamped_to_audio_and_neighbours():
    chunks = build_chunks([(0.05, 3), (3.7, 6)], 6.1)
    assert chunks[0].start == 0.0
    assert chunks[0].end <= (3 + 3.7) / 2 + 1e-6
    assert chunks[1].start >= (3 + 3.7) / 2 - 1e-6
    assert chunks[1].end <= 6.1


def test_ids_sequential_and_ordered():
    chunks = build_chunks([(0, 2), (3, 5), (6, 8)], 9.0)
    assert [c.id for c in chunks] == [1, 2, 3]
    assert all(a.end <= b.start for a, b in zip(chunks, chunks[1:]))
