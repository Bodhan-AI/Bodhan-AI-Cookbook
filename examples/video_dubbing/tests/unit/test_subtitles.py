from bodhan_dub.core.subtitle_engine import SubtitleEngine
from bodhan_dub.models.segment import DubSegment


def test_timestamp_formatting():
    engine = SubtitleEngine()
    # 1 hour, 2 minutes, 3 seconds, 450 ms
    secs = 3600 + 120 + 3 + 0.450
    srt_ts = engine.format_timestamp_srt(secs)
    vtt_ts = engine.format_timestamp_vtt(secs)

    assert srt_ts == "01:02:03,450"
    assert vtt_ts == "01:02:03.450"

def test_subtitle_generation(tmp_path):
    engine = SubtitleEngine()
    segments = [
        DubSegment(id="seg_0", start=0.0, end=2.5, source_text="Hello", translated_text="नमस्ते"),
        DubSegment(id="seg_1", start=3.0, end=5.2, source_text="World", translated_text="दुनिया")
    ]

    srt_file = tmp_path / "subs.srt"
    vtt_file = tmp_path / "subs.vtt"

    engine.generate_srt(segments, srt_file)
    engine.generate_vtt(segments, vtt_file)

    assert srt_file.exists()
    assert vtt_file.exists()

    srt_content = srt_file.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,500" in srt_content
    assert "नमस्ते" in srt_content
    assert "दुनिया" in srt_content

    vtt_content = vtt_file.read_text(encoding="utf-8")
    assert "WEBVTT" in vtt_content
    assert "00:00:00.000 --> 00:00:02.500" in vtt_content
