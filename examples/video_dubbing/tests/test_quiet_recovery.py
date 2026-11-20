"""Exercise quiet-tail recovery through the CLI with simulated hosted responses."""

from dataclasses import replace

import pytest
from test_integration import (
    LINES,
    SAMPLE_RATE,
    SimulatedProvider,
    acoustic_markers,
    create_video,
    decoded_audio,
    invoke_cli,
    load,
    read_cues,
)


def test_timing_overrun_reuses_speech_and_keeps_the_next_start(tmp_path, monkeypatch):
    first = replace(LINES[1], start=2.0, end=5.0)
    second = replace(LINES[2], start=7.2, end=10.2)
    video = create_video(tmp_path / "quiet-gap.mp4", (first, second))
    simulated = SimulatedProvider((first, second))
    first_text = first.translations["hi"]
    second_text = second.translations["hi"]
    # At 1.2× the 4.45-second tone still cannot fit inside the original speech
    # and standard 350 ms tail. The next source speech leaves room to recover.
    simulated.allow_synthesis("hi", first_text, 640, duration=4.45)
    out = tmp_path / "out"

    assert invoke_cli(
        monkeypatch, simulated, video, out, tmp_path / "state", languages=("hi",), excerpt=30,
    ) == 0
    assert [call.text for call in simulated.selected("tts", "hi")] == [first_text, second_text]
    assert sorted(call.text for call in simulated.selected("qa", "hi")) == sorted((first_text, second_text))
    assert [call.text for call in simulated.selected("translate", "hi")] == [first.english, second.english]

    recovered, following = load(out / "hi" / "segments.hi.json")
    baseline, retry = recovered["attempts"]
    assert baseline["action"] == "baseline"
    assert baseline["reason"] == "tts_cannot_fit_within_tempo_limit"
    assert baseline["qa"] is None
    assert retry["action"] == "cached_quiet_tail"
    assert retry["qa"]["passed"] and retry["qa"]["cer"] == 0
    assert 0.85 <= retry["clips"][0]["tempo"] <= 1.2
    assert [attempt["action"] for attempt in following["attempts"]] == ["baseline"]

    track = out / "hi" / "video.hi.mp4"
    markers = acoustic_markers(decoded_audio(track, duration=12), SAMPLE_RATE, [640, 720])
    assert set(markers) == {640, 720}
    assert markers[640][0] == pytest.approx(first.start, abs=0.300)
    assert first.end + 0.35 < markers[640][1] < markers[720][0]
    assert markers[720][0] == pytest.approx(second.start, abs=0.300)

    first_cue, second_cue = read_cues(out / "hi" / "subtitles.hi.srt")
    assert first_cue[2] == first_text
    assert second_cue[2] == second_text
    assert first.end + 0.35 < first_cue[1] <= second_cue[0]
    assert second_cue[0] == pytest.approx(second.start, abs=0.025)
