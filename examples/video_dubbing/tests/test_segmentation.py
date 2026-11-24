from bodhan_dub import segmentation as sg


def test_music_bed_is_not_speech():
    hop = 0.02
    sp = [-8.0, -14.0, -6.0, -16.0, -9.0]
    db = [-26.0] * 100 + sp * 20 + [-26.0] * 100 + sp * 30 + [-26.0] * 50   # constant music + 2 bursts
    spans = sg.energy_spans(db, hop)
    assert len(spans) == 2
    assert abs(spans[0][0] - 2.0) < 0.1 and abs(spans[0][1] - 4.0) < 0.1


def test_quiet_room_without_music():
    db = [-70.0] * 50 + [-15.0, -22.0, -12.0, -25.0] * 25 + [-70.0] * 50
    assert len(sg.energy_spans(db, 0.02)) == 1


def test_short_pauses_do_not_split_speech():
    db = [-8.0, -20.0, -6.0, -24.0, -9.0] * 10 + [-60.0] * 5 + [-8.0, -20.0, -6.0, -24.0, -9.0] * 10   # 100 ms pause
    assert len(sg.energy_spans(db, 0.02)) == 1


def test_chunks_respect_window_and_cut_at_pauses():
    spans = [(0, 10), (10.3, 20), (21.5, 30), (30.2, 40)]
    chunks, _ = sg.make_chunks(spans, 41.0, max_len=25.0, cut_gap=0.6)
    assert all(c["end"] - c["start"] <= 25.5 for c in chunks)
    # the only pause >= 0.6 s is between 20 and 21.5, so a cut must land there
    assert any(abs(c["end"] - 20.0) < 0.9 for c in chunks)


def test_continuous_speech_longer_than_window_is_hard_split_and_flagged():
    chunks, _ = sg.make_chunks([(0, 60)], 60.0, max_len=25.0)
    assert len(chunks) == 3 and all(c["hard_split"] for c in chunks)
    assert all(c["end"] - c["start"] <= 25.5 for c in chunks)


def test_tiny_blips_are_skipped_with_reason():
    chunks, skipped = sg.make_chunks([(0, 0.2), (5, 8)], 10.0)
    assert len(chunks) == 1 and skipped[0]["reason"] == "too_short_noise"


def test_heuristic_grouping_joins_fragments_until_sentence_end():
    texts = ["the area of a triangle is", "half the base times", "the height.", "next topic."]
    chunks = [{"start": 0, "end": 2}, {"start": 2.2, "end": 4}, {"start": 4.2, "end": 5}, {"start": 6, "end": 7}]
    assert sg.heuristic_groups(texts, chunks) == [[0, 1, 2], [3]]


def test_group_validation_rejects_bad_partitions():
    chunks = [{"start": i, "end": i + 1} for i in range(3)]
    assert sg.valid_groups([[0, 1], [2]], 3, chunks)
    assert not sg.valid_groups([[0, 1], [1, 2]], 3, chunks)    # overlap
    assert not sg.valid_groups([[1], [0], [2]], 3, chunks)     # order
    assert not sg.valid_groups([[0], [2]], 3, chunks)          # missing
    assert not sg.valid_groups([[0, 1, 2]], 3, [{"start": 0, "end": 10}, {"start": 10, "end": 20}, {"start": 20, "end": 30}])


def test_gaps_for():
    segs = [{"start": 1.0, "end": 3.0}, {"start": 3.5, "end": 5.0}]
    g = sg.gaps_for(segs, 8.0, margin=0.0)
    assert g[0] == (1.0, 0.5) and g[1] == (0.5, 3.0)


def test_flat_music_is_not_speech():
    db = [-20.0] * 200                      # a steady tone / hum with no syllable-level swings
    assert sg.energy_spans(db, 0.02) == []
    db2 = [-80.0] * 200                     # digital silence
    assert sg.energy_spans(db2, 0.02) == []


def test_plausibility_guard():
    assert sg.implausible_transcript("la " * 30, 5) is not None
    assert sg.implausible_transcript("the area of a triangle is half base times height", 4) is None
    assert sg.implausible_transcript("one two three four five six seven eight nine ten", 1) == "too_many_words_for_duration"


def test_long_run_is_split_at_a_real_pause():
    # one 30 s run of speech with a 0.4 s breath in the middle: split there instead of one huge chunk
    spans = [(0, 14), (14.4, 30)]
    chunks, _ = sg.make_chunks(spans, 31.0)
    assert len(chunks) == 2 and not any(c["hard_split"] for c in chunks)


def test_fallback_grouping_caps_unpunctuated_speech():
    chunks = [{"start": i * 14, "end": i * 14 + 13.5} for i in range(4)]
    groups = sg.heuristic_groups(["no punctuation here"] * 4, chunks)
    assert all(chunks[g[-1]]["end"] - chunks[g[0]]["start"] <= 15 for g in groups)
