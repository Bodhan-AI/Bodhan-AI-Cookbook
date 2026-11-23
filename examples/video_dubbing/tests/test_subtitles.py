from __future__ import annotations

from bodhan_dub import subtitles


def seg(start: float, end: float, value: str) -> dict:
    return {"start": start, "end": end, "translation": value, "source": ""}


def test_srt_has_numbered_cues_and_timestamps(tmp_path) -> None:
    path = tmp_path / "out.srt"
    count = subtitles.write_srt(path, [seg(3.2, 5.7, "अध्याय छह का पहला भाग")])

    text = path.read_text(encoding="utf-8")
    assert count == 1
    assert text.startswith("1\n")
    assert "00:00:03,200 --> 00:00:05,700" in text
    assert text.rstrip().endswith("अध्याय छह का पहला भाग")


def test_empty_translations_are_skipped(tmp_path) -> None:
    path = tmp_path / "out.srt"
    count = subtitles.write_srt(
        path, [seg(0.0, 1.0, ""), seg(1.2, 2.5, "पाठ"), seg(3.0, 4.0, "   ")]
    )

    assert count == 1


def test_short_cues_are_extended_to_the_minimum() -> None:
    cues = subtitles.build_cues([seg(1.0, 1.1, "हाँ"), seg(5.0, 6.0, "ठीक")])

    assert cues[0]["end"] - cues[0]["start"] >= subtitles.MIN_CUE_S
    assert cues[0]["end"] < 5.0


def test_cues_never_overlap() -> None:
    segments = [seg(0.0, 2.0, "एक"), seg(2.1, 4.0, "दो"), seg(4.2, 6.0, "तीन")]
    cues = subtitles.build_cues(segments)

    for earlier, later in zip(cues, cues[1:], strict=False):
        assert earlier["end"] <= later["start"]


def test_long_text_wraps_to_at_most_two_lines() -> None:
    long_text = " ".join(["शब्द"] * 60)
    cues = subtitles.build_cues([seg(0.0, 5.0, long_text)])

    assert len(cues[0]["text"].splitlines()) == 2


def test_short_text_is_one_line() -> None:
    cues = subtitles.build_cues([seg(0.0, 2.0, "छोटा वाक्य")])

    assert len(cues[0]["text"].splitlines()) == 1


def test_timestamp_formatting() -> None:
    assert subtitles._timestamp(0.0) == "00:00:00,000"
    assert subtitles._timestamp(61.5) == "00:01:01,500"
    assert subtitles._timestamp(3661.25) == "01:01:01,250"


def test_unsorted_segments_are_sorted() -> None:
    cues = subtitles.build_cues([seg(4.0, 5.0, "दो"), seg(0.0, 1.0, "एक")])

    assert cues[0]["text"] == "एक"
