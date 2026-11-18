"""srt.py — cue allocation and the SRT invariants the verifier checks."""
from __future__ import annotations

from bodhan_agent import srt

CHUNKS = [
    {
        "start_s": 0.0,
        "end_s": 25.0,
        "text": "The first window has three short sentences. The second one is here. And this is the third.",
    },
    {
        "start_s": 25.0,
        "end_s": 55.0,
        "text": "This window carries a considerably longer sentence that has to be broken across cues because "
        "it will not fit inside two lines of forty-two characters each, and then it ends.",
    },
]


def test_cues_respect_every_limit():
    built = srt.cues(CHUNKS)
    assert built, "a non-empty transcript must produce at least one cue"
    previous_end = 0.0
    for cue in built:
        assert cue["start_s"] >= previous_end, "cues must be monotonic and non-overlapping"
        assert cue["end_s"] >= cue["start_s"]
        previous_end = cue["end_s"]
        lines = cue["text"].split("\n")
        assert 1 <= len(lines) <= 2
        assert all(len(line) <= 42 for line in lines)
        assert cue["text"].strip(), "cue text must not be empty"
    assert built[0]["start_s"] == 0.0
    assert abs(built[-1]["end_s"] - 55.0) < 0.001


def test_sentence_boundaries_are_preferred():
    built = srt.cues([{"start_s": 0.0, "end_s": 9.0, "text": "One. Two. Three."}])
    assert [cue["text"] for cue in built] == ["One.", "Two.", "Three."]


def test_overlong_word_never_breaks_the_line_limit():
    built = srt.cues([{"start_s": 0.0, "end_s": 4.0, "text": "x" * 90}])
    assert built
    assert all(len(line) <= 42 for cue in built for line in cue["text"].split("\n"))
    assert all(len(cue["text"].split("\n")) <= 2 for cue in built)


def test_to_srt_shape():
    built = srt.cues(CHUNKS[:1])
    text = srt.to_srt(built)
    assert text.startswith("1\n00:00:00,000 --> ")
    assert text.endswith("\n")
    blocks = text.strip().split("\n\n")
    assert len(blocks) == len(built)
    for index, block in enumerate(blocks, 1):
        assert block.splitlines()[0] == str(index)


def test_write_srt_roundtrip(tmp_path):
    built = srt.cues(CHUNKS)
    target = srt.write_srt(built, tmp_path / "out" / "subtitles.srt")
    assert target == str(tmp_path / "out" / "subtitles.srt")
    assert (tmp_path / "out" / "subtitles.srt").read_text(encoding="utf-8") == srt.to_srt(built)


def test_empty_transcript_has_no_cues():
    assert srt.cues([]) == []
    assert srt.cues([{"start_s": 0.0, "end_s": 4.0, "text": "   "}]) == []
    assert srt.to_srt([]) == ""
