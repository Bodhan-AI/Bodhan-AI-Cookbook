"""End-to-end smoke test on a 30-second clip.

No network: the ASR call is replaced with a deterministic stub, so this test
exercises the real path — ffmpeg decode, VAD, chunk planning, the CLI, the
transcript writer, the report writer and exit codes — without spending a rupee.

Set ``BODHAN_TRANSCRIBE_KEY`` and drop ``sample_data/smoke.wav`` in place to
also exercise the live model; see ``test_e2e_live.py``.
"""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from bodhan_dub import cli, media
from bodhan_dub import client as client_module
from bodhan_dub import qa as qa_module
from bodhan_dub.config import Settings
from bodhan_dub.errors import MediaError
from tests.conftest import SR, build_signal

DURATION_S = 30.0
SPEECH = [(0.5, 2.5), (3.5, 6.0), (8.0, 11.0), (13.0, 16.5), (18.0, 21.0), (23.0, 26.0), (27.5, 29.5)]


def _ffmpeg_available() -> bool:
    try:
        media.ensure_tools()
    except MediaError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe not installed")


@pytest.fixture
def clip(tmp_path):
    wav = tmp_path / "speech.wav"
    media.write_wav(wav, build_signal(SPEECH, total_s=DURATION_S), SR)
    out = tmp_path / "clip.mp4"
    subprocess.run(
        [
            media.find_binary("ffmpeg"),
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=160x120:r=10:d={int(DURATION_S)}",
            "-i",
            str(wav),
            "-c:v",
            "mpeg4",
            "-q:v",
            "6",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _stub_client(monkeypatch) -> None:
    def fake_transcribe(
        self,
        wav_bytes,
        *,
        language,
        audio_seconds,
        filename="chunk.wav",
        stage="asr",
        item=None,
        segment=None,
    ):
        marker = item if item is not None else segment
        return client_module.Result(
            data={"text": f"this is segment number {marker} of the lesson"},
            cached=False,
            latency_ms=1.0,
            attempts=1,
        )

    def fake_translate(
        self,
        text,
        *,
        target_language,
        script=None,
        max_output_tokens=None,
        stage="translate",
        item=None,
    ):
        translated = " ".join(text.split())
        return client_module.Result(
            data={"translation": translated, "output_tokens": len(text.split())},
            cached=False,
            latency_ms=1.0,
            attempts=1,
        )

    def fake_speak(
        self, text, *, voice, language, style=None, stage="speak", item=None
    ):
        seconds = max(0.35, len(text) / 12.0)
        samples = np.zeros(int(SR * seconds), dtype=np.float32)
        samples[int(0.02 * SR) :] = 0.2
        return client_module.SpeechResult(
            audio=media.to_wav_bytes(samples, SR),
            cached=False,
            latency_ms=1.0,
            attempts=1,
        )

    def passing_qa(expected, heard, **kwargs):
        return qa_module.SpeechQA(
            cer=0.0,
            repetition=False,
            truncation=1.0,
            verdict="pass",
            reason=None,
            heard=heard,
        )

    monkeypatch.setattr(client_module.BodhanClient, "transcribe", fake_transcribe)
    monkeypatch.setattr(client_module.BodhanClient, "translate", fake_translate)
    monkeypatch.setattr(client_module.BodhanClient, "speak", fake_speak)
    monkeypatch.setattr(qa_module, "evaluate", passing_qa)


def _stub_settings(tmp_path):
    def factory() -> Settings:
        return Settings(
            base_url="https://api.test",
            keys={"transcribe": "k", "translate": "k", "speak": "k", "ocr": "k"},
            cache_dir=tmp_path / "cache",
        )

    return factory


def test_asr_stage_produces_a_usable_transcript(tmp_path, clip, monkeypatch) -> None:
    _stub_client(monkeypatch)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    code = cli.main(
        ["--input", str(clip), "--langs", "hi,ta,bn", "--out", str(out), "--stage", "asr"]
    )

    assert code == 0
    transcript = json.loads((out / "transcript.en.json").read_text(encoding="utf-8"))
    assert transcript["schema"] == "bodhan-dub/transcript/v1"
    assert transcript["asr"]["timing"] == "vad-chunk-boundaries"
    assert transcript["stats"]["segments"] >= 5
    assert transcript["stats"]["failures"] == 0
    assert 0.0 < transcript["stats"]["speech_coverage"] <= 1.0

    for segment in transcript["segments"]:
        assert segment["end"] > segment["start"]
        assert segment["text"].strip()

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["asr"]["status"] == "ok"
    assert report["run"]["exit_code"] == 0
    assert set(report["languages"]) == {"hi", "ta", "bn"}
    assert (out / "run.log").exists()


def test_transcript_segments_do_not_overlap_and_are_ordered(tmp_path, clip, monkeypatch) -> None:
    _stub_client(monkeypatch)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    cli.main(["--input", str(clip), "--langs", "hi", "--out", str(out), "--stage", "asr"])

    transcript = json.loads((out / "transcript.en.json").read_text(encoding="utf-8"))
    segments = transcript["segments"]
    for earlier, later in zip(segments, segments[1:], strict=False):
        assert earlier["end"] <= later["start"] + 1e-3


def test_full_run_produces_video_and_subtitles(tmp_path, clip, monkeypatch) -> None:
    _stub_client(monkeypatch)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    code = cli.main(["--input", str(clip), "--langs", "hi", "--out", str(out)])

    assert code == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["assemble"]["status"] == "ok"
    assert (out / "transcript.en.json").exists()
    assert (out / "hi" / "segments.hi.json").exists()
    assert (out / "hi" / "video.hi.mp4").exists()
    assert (out / "hi" / "subtitles.hi.srt").exists()
    # Duration and loudness are measured, not marked "not_measured" any more.
    targets = report["quality_targets"]
    assert targets["total_length"]["status"] in {"pass", "fail"}
    assert targets["loudness"]["status"] in {"pass", "partial"}

    srt = (out / "hi" / "subtitles.hi.srt").read_text(encoding="utf-8")
    assert "-->" in srt


def test_speak_stage_dubs_every_segment(tmp_path, clip, monkeypatch) -> None:
    _stub_client(monkeypatch)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    code = cli.main(
        ["--input", str(clip), "--langs", "hi", "--out", str(out), "--stage", "speak"]
    )

    assert code == 0
    payload = json.loads((out / "hi" / "segments.hi.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "bodhan-dub/segments/v1"
    assert payload["stats"]["segments"] >= 5
    assert payload["stats"]["ok"] >= 5
    assert payload["stats"]["failed"] == 0
    assert payload["voice"]
    for segment in payload["segments"]:
        assert segment["translation"]
        assert segment["status"] in {"ok", "skipped"}
        if segment["status"] == "ok":
            assert segment["fit"]["tempo"] <= 1.2 + 1e-6


def test_code_mixed_is_recorded(tmp_path, clip, monkeypatch) -> None:
    _stub_client(monkeypatch)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    cli.main(
        [
            "--input",
            str(clip),
            "--langs",
            "hi",
            "--out",
            str(out),
            "--stage",
            "translate",
            "--code-mixed",
        ]
    )

    payload = json.loads((out / "hi" / "segments.hi.json").read_text(encoding="utf-8"))
    assert payload["code_mixed"] is True


def test_missing_input_is_a_usage_error(tmp_path) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--input", str(tmp_path / "nope.mp4"), "--langs", "hi", "--out", str(tmp_path)])
    assert caught.value.code == 2


def test_rejected_key_stops_the_run_early(tmp_path, clip, monkeypatch) -> None:
    from bodhan_dub.errors import BodhanAPIError

    calls = {"count": 0}

    def rejecting(
        self,
        wav_bytes,
        *,
        language,
        audio_seconds,
        filename="chunk.wav",
        stage="asr",
        segment=None,
    ):
        calls["count"] += 1
        raise BodhanAPIError(401, "unauthorized", "bad key")

    monkeypatch.setattr(client_module.BodhanClient, "transcribe", rejecting)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    code = cli.main(["--input", str(clip), "--langs", "hi", "--out", str(out), "--stage", "asr"])

    assert code == 2
    # One request per in-flight worker at most, not one per chunk.
    assert calls["count"] < len(SPEECH)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["asr"]["status"] == "failed"
    assert "rejected" in report["errors"][0]


def test_bad_language_is_a_usage_error(tmp_path, clip) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--input", str(clip), "--langs", "fr", "--out", str(tmp_path)])
    assert caught.value.code == 2


def test_a_partial_transcript_still_produces_a_dub(tmp_path, clip, monkeypatch) -> None:
    from bodhan_dub.errors import BodhanAPIError

    _stub_client(monkeypatch)

    def flaky(
        self,
        wav_bytes,
        *,
        language,
        audio_seconds,
        filename="chunk.wav",
        stage="asr",
        item=None,
        segment=None,
    ):
        marker = item if item is not None else segment
        if marker == 2:
            raise BodhanAPIError(400, "invalid_request_error", "simulated bad chunk")
        return client_module.Result(
            data={"text": f"this is segment number {marker} of the lesson"},
            cached=False,
            latency_ms=1.0,
            attempts=1,
        )

    monkeypatch.setattr(client_module.BodhanClient, "transcribe", flaky)
    monkeypatch.setattr(cli, "load_settings", _stub_settings(tmp_path))
    out = tmp_path / "out"

    code = cli.main(
        ["--input", str(clip), "--langs", "hi", "--out", str(out), "--stage", "speak"]
    )

    # The run fails at the end, but the artefacts still exist.
    assert code == 3
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["stages"]["asr"]["status"] == "partial"
    payload = json.loads((out / "hi" / "segments.hi.json").read_text(encoding="utf-8"))
    assert payload["stats"]["segments"] >= 4
    assert (out / "hi" / "audio.hi.wav").exists()
