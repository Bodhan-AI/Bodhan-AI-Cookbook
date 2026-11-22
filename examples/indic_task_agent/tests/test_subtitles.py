from bodhan_mcp.subtitles import format_timestamp, segments_to_srt


def test_format_timestamp():
    assert format_timestamp(0) == "00:00:00,000"
    assert format_timestamp(3.5) == "00:00:03,500"
    assert format_timestamp(3725.042) == "01:02:05,042"
    assert format_timestamp(-1) == "00:00:00,000"


def test_srt_output_exact():
    segments = [
        {"start": 0.0, "end": 2.5, "text": "Hello there."},
        {"start": 2.5, "end": 5.0, "text": "  How   are you? "},
    ]
    assert segments_to_srt(segments) == (
        "1\n00:00:00,000 --> 00:00:02,500\nHello there.\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\nHow are you?\n"
    )


def test_empty_cues_skipped_and_numbering_continues():
    segments = [
        {"start": 0, "end": 1, "text": "one"},
        {"start": 1, "end": 2, "text": "   "},
        {"start": 2, "end": 3, "text": "two"},
    ]
    srt = segments_to_srt(segments)
    assert srt.count("-->") == 2
    assert "\n\n2\n00:00:02,000" in srt


def test_overlap_and_short_cues_are_fixed():
    segments = [
        {"start": 0, "end": 3, "text": "first"},
        {"start": 2, "end": 2.1, "text": "second"},
    ]
    lines = segments_to_srt(segments).splitlines()
    assert "00:00:03,000 --> 00:00:03,500" in lines  # moved after first, stretched to 0.5 s


def test_long_text_wraps():
    srt = segments_to_srt([{"start": 0, "end": 4, "text": "word " * 30}], max_chars_per_line=20)
    assert all(len(line) <= 20 for line in srt.splitlines()[2:] if line)


def test_no_segments():
    assert segments_to_srt([]) == ""
