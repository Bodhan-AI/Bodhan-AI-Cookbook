import wave

from bodhan_dub.core.audio_sync import AudioSyncEngine
from bodhan_dub.models.segment import DubSegment


def test_atempo_filter_builder():
    engine = AudioSyncEngine()
    # Within single stage 0.5 - 2.0
    f1 = engine.build_atempo_filter(1.15)
    assert f1 == "atempo=1.150000"

    # Multi-stage above 2.0
    f2 = engine.build_atempo_filter(3.0)
    assert "atempo=2.000000,atempo=1.500000" in f2

    # Multi-stage below 0.5
    f3 = engine.build_atempo_filter(0.4)
    assert "atempo=0.500000" in f3

def test_timeline_assembly(tmp_path):
    engine = AudioSyncEngine()
    seg_dir = tmp_path / "fitted"
    seg_dir.mkdir()

    # Create 1-second segment wav
    seg_wav = seg_dir / "seg_001_fitted.wav"
    with wave.open(str(seg_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x20\x20" * 16000)

    seg = DubSegment(
        id="seg_001",
        start=1.0,
        end=2.0,
        source_text="Test",
        fitted_audio_path=str(seg_wav)
    )

    out_timeline = tmp_path / "timeline.wav"
    engine.assemble_timeline([seg], total_video_duration=5.0, output_timeline_path=out_timeline)

    assert out_timeline.exists()
    with wave.open(str(out_timeline), "rb") as wf:
        dur = wf.getnframes() / float(wf.getframerate())
        assert abs(dur - 5.0) < 0.01  # Within 10ms
