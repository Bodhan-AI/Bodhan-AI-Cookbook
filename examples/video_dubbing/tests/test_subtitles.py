"""Subtitle timestamp formatting and SRT/VTT structure."""

from bodhan_dub.pipeline.subtitles import _ts, write_srt, write_vtt
from bodhan_dub.types import DubSegment


def test_srt_timestamp_format():
    assert _ts(3661.5, ",") == "01:01:01,500"
    assert _ts(0.0, ",") == "00:00:00,000"


def test_vtt_uses_dot_separator():
    assert _ts(5.25, ".") == "00:00:05,250".replace(",", ".")


def test_write_srt_numbers_and_skips_empty(tmp_path):
    dubs = [
        DubSegment(index=0, start=0.0, end=2.0, source_text="hi", translation="namaste"),
        DubSegment(index=1, start=2.0, end=4.0, source_text="x", translation=""),  # skipped
        DubSegment(index=2, start=4.0, end=6.0, source_text="bye", translation="alvida"),
    ]
    p = tmp_path / "s.srt"
    write_srt(dubs, str(p))
    text = p.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,000" in text
    assert "namaste" in text and "alvida" in text
    # empty translation is omitted and numbering stays contiguous (1, then 2)
    assert text.splitlines()[0] == "1"
    assert "\n2\n" in text


def test_write_vtt_has_header(tmp_path):
    p = tmp_path / "s.vtt"
    write_vtt([DubSegment(index=0, start=0.0, end=1.0, source_text="a", translation="b")], str(p))
    assert p.read_text(encoding="utf-8").startswith("WEBVTT")
