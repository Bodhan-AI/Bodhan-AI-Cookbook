from __future__ import annotations

from bodhan_dub.translate import align_lines, build_blocks, split_translation


def seg(sid: int, start: float, end: float, value: str) -> dict:
    return {"id": sid, "start": start, "end": end, "duration_s": round(end - start, 3), "text": value}


def test_short_fragments_are_merged_into_one_block() -> None:
    segments = [
        seg(0, 0.0, 0.5, "Here"),
        seg(1, 0.6, 1.8, "the points A"),
        seg(2, 1.9, 4.0, "O and B are collinear"),
    ]
    blocks = build_blocks(segments, max_chars=250)

    assert len(blocks) == 1
    assert blocks[0].segment_ids == (0, 1, 2)
    assert "collinear" in blocks[0].text


def test_block_text_uses_one_line_per_segment() -> None:
    segments = [seg(0, 0.0, 1.0, "first line"), seg(1, 1.1, 2.0, "second line")]
    block = build_blocks(segments, max_chars=250)[0]

    assert block.text == "first line\nsecond line"


def test_align_lines_matches_one_output_line_per_segment() -> None:
    aligned = align_lines("पहली पंक्ति\nदूसरी पंक्ति\nतीसरी", 3)

    assert aligned == ["पहली पंक्ति", "दूसरी पंक्ति", "तीसरी"]


def test_align_lines_ignores_blank_lines() -> None:
    aligned = align_lines("पहली\n\nदूसरी\n", 2)

    assert aligned == ["पहली", "दूसरी"]


def test_align_lines_returns_none_when_the_count_mismatches() -> None:
    assert align_lines("one line only", 3) is None


def test_align_lines_collapses_output_for_a_single_segment() -> None:
    assert align_lines("line one\nline two", 1) == ["line one line two"]


def test_align_lines_returns_none_for_empty_output() -> None:
    assert align_lines("   ", 1) is None


def test_a_sentence_boundary_closes_a_block_once_it_is_long_enough() -> None:
    long_enough = "this sentence is comfortably longer than the minimum block size."
    segments = [
        seg(0, 0.0, 3.0, long_enough),
        seg(1, 3.2, 6.0, "and here is a second complete thought"),
    ]
    blocks = build_blocks(segments, max_chars=250)

    assert len(blocks) == 2


def test_block_respects_the_character_cap() -> None:
    segments = [seg(i, i * 2.0, i * 2.0 + 1.9, "word " * 20) for i in range(6)]
    blocks = build_blocks(segments, max_chars=120)

    assert len(blocks) > 1
    for block in blocks:
        assert len(block.text) <= 120 + 6


def test_every_segment_appears_exactly_once() -> None:
    segments = [seg(i, i * 1.5, i * 1.5 + 1.4, f"phrase number {i}") for i in range(12)]
    blocks = build_blocks(segments, max_chars=80)

    seen = [sid for block in blocks for sid in block.segment_ids]
    assert sorted(seen) == list(range(12))


def test_split_single_slot_returns_everything() -> None:
    assert split_translation("whole sentence here", [2.0]) == ["whole sentence here"]


def test_split_never_cuts_a_word() -> None:
    pieces = split_translation("one two three four five six seven eight", [1.0, 1.0, 1.0])

    assert len(pieces) == 3
    assert " ".join(pieces).split() == "one two three four five six seven eight".split()
    for piece in pieces:
        assert piece and piece == piece.strip()


def test_split_follows_the_weights() -> None:
    pieces = split_translation(
        "alpha beta gamma delta epsilon zeta eta theta", [3.0, 1.0, 1.0]
    )

    assert len(pieces[0].split()) > len(pieces[1].split())


def test_split_gives_every_slot_a_word_when_it_can() -> None:
    pieces = split_translation("one two three four", [1.0, 1.0, 1.0, 1.0])

    assert all(piece for piece in pieces)
    assert len(pieces) == 4


def test_split_leaves_slots_empty_when_there_are_too_few_words() -> None:
    pieces = split_translation("only two", [1.0, 1.0, 1.0])

    assert len(pieces) == 3
    assert pieces[0] == "only"
    assert pieces[1] == "two"
    assert pieces[2] == ""


def test_split_of_empty_translation_is_all_empty() -> None:
    assert split_translation("", [1.0, 2.0]) == ["", ""]


def test_split_handles_zero_and_negative_weights() -> None:
    pieces = split_translation("one two three four", [0.0, -1.0, 2.0])

    assert len(pieces) == 3
    assert " ".join(pieces).split() == ["one", "two", "three", "four"]
