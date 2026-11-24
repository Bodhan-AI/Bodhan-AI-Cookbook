"""
Unit tests for subtitles and transcript formatting.
"""

from bodhan_dub.stages.segment import SegmentSlot
from bodhan_dub.stages.subtitles import (
    format_srt_timestamp,
    format_vtt_timestamp,
    write_srt,
    write_transcript_en_json,
    write_vtt,
)
from bodhan_dub.text.graphemes import get_grapheme_clusters, wrap_text_graphemes


def test_timestamp_formatting():
    # 83.456 seconds = 1 min, 23.456 sec
    assert format_srt_timestamp(83.456) == "00:01:23,456"
    assert format_vtt_timestamp(83.456) == "00:01:23.456"

    # 3661.050 seconds = 1 hour, 1 min, 1.05 sec
    assert format_srt_timestamp(3661.050) == "01:01:01,050"
    assert format_vtt_timestamp(3661.050) == "01:01:01.050"


def test_grapheme_clusters_indic():
    # Devanagari conjunct: स् + त + े = स्ते is 1 extended grapheme cluster \X
    # So नमस्ते has 3 clusters: न, म, स्ते
    clusters = get_grapheme_clusters("नमस्ते")
    assert len(clusters) == 3
    assert clusters == ["न", "म", "स्ते"]


def test_wrap_text_graphemes():
    long_text = "This is a sentence that wraps across lines."
    wrapped = wrap_text_graphemes(long_text, max_line_chars=25, max_lines=2)
    lines = wrapped.split("\n")
    assert len(lines) == 2
    assert lines[0] == "This is a sentence that"
    assert lines[1] == "wraps across lines."


def test_write_transcript_and_subtitles(tmp_path):
    slots = [
        SegmentSlot(
            id="seg_0001",
            index=0,
            text="Welcome to artificial intelligence.",
            start=1.0,
            end=4.5,
            duration=3.5,
            slot=[1.0, 4.5],
            slack_before=0.5,
            slack_after=0.42,
            timestamp_source="api_verbose",
        )
    ]

    srt_path = tmp_path / "subtitles.en.srt"
    write_srt(slots, srt_path)
    assert srt_path.exists()
    srt_content = srt_path.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:04,500" in srt_content
    assert "Welcome to artificial intelligence." in srt_content

    vtt_path = tmp_path / "subtitles.en.vtt"
    write_vtt(slots, vtt_path)
    assert vtt_path.exists()
    vtt_content = vtt_path.read_text(encoding="utf-8")
    assert "WEBVTT" in vtt_content
    assert "00:00:01.000 --> 00:00:04.500" in vtt_content

    json_path = tmp_path / "transcript.en.json"
    write_transcript_en_json(slots, json_path)
    assert json_path.exists()
    import json

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["id"] == "seg_0001"
    assert data[0]["slot"] == [1.0, 4.5]


def test_target_subtitles_omits_skipped_and_matches_placed_count(tmp_path):
    """B6: Subtitles use placed times and omit skipped/failed slots."""
    from bodhan_dub.stages.subtitles import write_target_segments_json, write_target_subtitles

    segments_data = [
        {
            "segment_id": "seg_01",
            "source_text": "Hello world.",
            "target_text": "नमस्ते दुनिया।",
            "status": "ok",
            "cer": 0.02,
            "duration_raw": 1.5,
            "duration_placed": 1.5,
            "speed_factor": 1.0,
            "offset_applied_ms": 10.0,
            "qa_verdict": "passed",
            "retry_count": 0,
            "cost_inr": 0.005,
            "start": 1.01,
            "end": 2.51,
        },
        {
            "segment_id": "seg_02",
            "source_text": "Failed text.",
            "target_text": "",
            "status": "skipped",
            "cer": 1.0,
            "duration_raw": 0.0,
            "duration_placed": 0.0,
            "speed_factor": 1.0,
            "offset_applied_ms": 0.0,
            "qa_verdict": "skipped",
            "retry_count": 0,
            "cost_inr": 0.0,
            "start": 3.0,
            "end": 4.0,
        },
        {
            "segment_id": "seg_03",
            "source_text": "Third sentence.",
            "target_text": "तीसरा वाक्य।",
            "status": "ok",
            "cer": 0.01,
            "duration_raw": 1.2,
            "duration_placed": 1.2,
            "speed_factor": 1.0,
            "offset_applied_ms": 0.0,
            "qa_verdict": "passed",
            "retry_count": 0,
            "cost_inr": 0.004,
            "start": 4.5,
            "end": 5.7,
        },
    ]

    srt_path = tmp_path / "subtitles.hi.srt"
    vtt_path = tmp_path / "subtitles.hi.vtt"
    write_target_subtitles(segments_data, srt_path, vtt_path)

    srt_text = srt_path.read_text(encoding="utf-8")
    vtt_text = vtt_path.read_text(encoding="utf-8")

    # Exactly 2 cues emitted (1 and 2), skipped slot omitted!
    assert "1\n00:00:01,010 --> 00:00:02,510\nनमस्ते दुनिया।" in srt_text
    assert "2\n00:00:04,500 --> 00:00:05,700\nतीसरा वाक्य।" in srt_text
    assert "Failed text" not in srt_text
    assert "WEBVTT" in vtt_text
    assert "नमस्ते दुनिया।" in vtt_text

    # Verify segments.json schema
    seg_json_path = tmp_path / "segments.hi.json"
    write_target_segments_json(segments_data, seg_json_path)
    import json
    loaded = json.loads(seg_json_path.read_text(encoding="utf-8"))
    assert len(loaded) == 3
    required_keys = {
        "source_text", "target_text", "status", "cer", "duration_raw",
        "duration_placed", "speed_factor", "offset_applied_ms",
        "qa_verdict", "retry_count", "cost_inr"
    }
    for item in loaded:
        assert required_keys.issubset(item.keys())
