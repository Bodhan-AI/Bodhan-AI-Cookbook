"""Segmentation: speech regions become sentence units without moving words or bridging long pauses."""

from bodhan_dub.transcript import MAX_GAP, build_units, same_length, small_numbers_kept

SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 2.0, "text": "first word"},
    {"id": 1, "start": 2.4, "end": 5.0, "text": "three or more points that lie on a line"},
    {"id": 2, "start": 5.3, "end": 6.0, "text": "are collinear"},
    {"id": 3, "start": 15.0, "end": 17.0, "text": "write it down"},
]


def reply(ends):
    texts = ["First word:", "Three or more points that lie on a line", "are collinear.", "Write it down."]
    return {"segments": [{"id": i, "text": t, "ends": e} for i, (t, e) in enumerate(zip(texts, ends, strict=True))]}


def test_units_follow_sentence_ends():
    units = build_units(SEGMENTS, reply([True, False, True, True]))
    assert [u["segments"] for u in units] == [[0], [1, 2], [3]]
    assert units[1]["start"] == 2.4 and units[1]["end"] == 6.0
    assert units[1]["text"] == "Three or more points that lie on a line are collinear."


def test_a_long_pause_always_ends_a_unit():
    units = build_units(SEGMENTS, reply([False, False, False, True]))
    assert SEGMENTS[3]["start"] - SEGMENTS[2]["end"] > MAX_GAP
    assert [u["segments"] for u in units] == [[0, 1, 2], [3]]


def test_llm_failure_falls_back_to_one_raw_unit_per_segment():
    units = build_units(SEGMENTS, {})
    assert [u["text"] for u in units] == [s["text"] for s in SEGMENTS]


def test_moved_words_are_rejected_for_the_raw_text():
    bad = {"segments": [{"id": 0, "text": "First word: three or more points that lie on a line", "ends": True}]}
    assert build_units(SEGMENTS[:1], bad)[0]["text"] == "first word"


def test_same_length_allows_numbers_to_shrink():
    assert same_length("110 degrees.", "one hundred and ten degrees")
    assert not same_length("Hi.", "this sentence has many more words than that")


def test_invented_small_numbers_are_caught():
    assert not small_numbers_kept("2, 2.", "three two")
    assert small_numbers_kept("3, 2.", "three two")
    assert small_numbers_kept("180 degrees", "one eighty degrees")


def test_a_lowercase_next_segment_continues_the_sentence():
    segs = [{"id": 0, "start": 0.0, "end": 2.0, "text": "same vertex one common arm"},
            {"id": 1, "start": 2.4, "end": 5.0, "text": "and lie on opposite sides"}]
    marked_ends = {"segments": [{"id": 0, "text": "Same vertex, one common arm.", "ends": True},
                                {"id": 1, "text": "and lie on opposite sides.", "ends": True}]}
    units = build_units(segs, marked_ends)
    assert [u["text"] for u in units] == ["Same vertex, one common arm and lie on opposite sides."]


def test_a_long_pause_is_never_joined_even_in_lowercase():
    segs = [{"id": 0, "start": 0.0, "end": 2.0, "text": "first part"},
            {"id": 1, "start": 2.0 + MAX_GAP + 0.5, "end": 6.0, "text": "and more"}]
    reply = {"segments": [{"id": 0, "text": "First part.", "ends": True}, {"id": 1, "text": "and more.", "ends": True}]}
    assert len(build_units(segs, reply)) == 2
