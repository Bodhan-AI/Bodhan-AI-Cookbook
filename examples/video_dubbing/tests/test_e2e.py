"""End to end on a 30-second clip.

* `test_pipeline_offline_end_to_end` always runs: the real pipeline (VAD stubbed to fixed
  regions, since a test tone is not speech) with a scripted Bodhan client and LLM. It
  checks the output layout, the report fields and per-language isolation, with no network.
* `test_cli_live_30s` is the real thing against the hosted APIs: `pytest -m live`.
  It needs keys in .env and costs about ₹1. Set BODHAN_DUB_SMOKE_VIDEO to any English video.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from bodhan_dub import audio, pipeline, vad
from bodhan_dub.bodhan import BodhanError, Translation, UsageMeter, wav_duration
from bodhan_dub.config import Settings
from bodhan_dub.pipeline import RunOptions

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

SETTINGS = Settings.from_env({"BODHAN_API_KEY": "k", "LLM_BASE_URL": "http://x", "LLM_MODEL": "m", "LLM_API_KEY": "k"})
ENGLISH = ["Today we learn about angles.", "An angle is made by two rays.", "Write it in your notebook."]
HINDI = {e: h for e, h in zip(ENGLISH, ["आज हम कोणों के बारे में सीखेंगे।", "कोण दो किरणों से बनता है।",
                                        "इसे अपनी कॉपी में लिखिए।"], strict=True)}


class FakeBodhan:
    """Scripted Bodhan: speech length encodes the text so back-transcription can find it again."""

    def __init__(self, fail_lang=None, mute_lang=None, deaf=False):
        self.usage = UsageMeter()
        self.deaf = deaf  # every transcription fails (bad key, no credit)
        self.fail_lang = fail_lang  # translate crashes outright
        self.mute_lang = mute_lang  # every speak call fails with an API error
        self.spoken: dict[float, str] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.chunk = 0

    def transcribe(self, wav, language="en"):
        if self.deaf:
            raise BodhanError(429, "budget_exceeded", "Bodhan credit exhausted for this key")
        if language == "en":
            text = ENGLISH[self.chunk % len(ENGLISH)]
            self.chunk += 1
            return text
        if not self.spoken:
            return ""
        heard = wav_duration(wav)
        return self.spoken[min(self.spoken, key=lambda seconds: abs(seconds - heard))]

    def translate(self, text, target_language, script="native"):
        if target_language == self.fail_lang:
            raise RuntimeError("simulated crash in one language")
        if target_language == "en":
            return Translation({h: e for e, h in HINDI.items()}.get(text, text), False, 1)
        return Translation(" ".join(HINDI.get(s, s) for s in [text]), False, 1)

    def speak(self, text, voice, lang, style=None, variant=0):
        if lang == self.mute_lang:
            raise BodhanError(503, "internal_error", "speech service down")
        # Each distinct text gets its own length, 0.3 s apart, so a clip can be matched back to its
        # text even after silence trimming, whatever order parallel calls run in.
        with self.lock:
            if text not in self.order:
                self.order.append(text)
            seconds = 1.0 + 0.3 * self.order.index(text)
            self.spoken[seconds] = text
        t = np.arange(int(seconds * 24_000)) / 24_000
        return audio.array_to_wav((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24_000)


class FakeLLM:
    model = "fake"
    usage = {"calls": 0}

    def complete_json(self, system, user):
        if "clean up raw English" in system:
            ids = [s["id"] for s in json.loads(user.split("\n\n")[0])["segments"]]
            texts = [s["text"] for s in json.loads(user.split("\n\n")[0])["segments"]]
            return {"units": [{"ids": [i], "text": t} for i, t in zip(ids, texts, strict=True)]}
        if "speaking style" in system:
            return {"style": "educational lecture"}
        return {"terms": []} if "terminology" in system else {"verdicts": []}


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=30",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


@pytest.fixture
def fixed_speech(monkeypatch):
    regions = [vad.Region(1.0, 6.0), vad.Region(9.0, 14.0), vad.Region(18.0, 23.0)]

    def detect(samples, params=None, backend="auto"):
        return vad.VadResult(regions=regions, probs=np.zeros(1000, np.float32), backend="stub",
                             duration_s=len(samples) / vad.SAMPLE_RATE)

    monkeypatch.setattr(pipeline.vad, "detect_speech", detect)


def test_pipeline_offline_end_to_end(tmp_path, clip, fixed_speech):
    out = tmp_path / "out"
    opts = RunOptions(input=clip, langs=["hi", "ta", "bn"], out=out, cache_dir=out / ".cache")
    result = pipeline.run(opts, SETTINGS, client=FakeBodhan(fail_lang="ta", mute_lang="bn"), llm=FakeLLM())

    # Layout required by the brief.
    for name in ("report.json", "transcript.en.json", "hi/video.hi.mp4", "hi/subtitles.hi.srt", "hi/segments.hi.json"):
        assert (out / name).exists(), name
    report = json.loads((out / "report.json").read_text())
    hi = report["languages"]["hi"]
    assert hi["status"] == "ok" and hi["coverage"]["dubbed"] == 3
    assert hi["sync_ms"]["within_300ms"] == 3
    assert abs(hi["total_length_ms"]) <= 100
    assert -17 <= hi["speech_lufs"] <= -15

    # One language crashing does not stop the other, and partial output is still written.
    assert result.failed == ["ta", "bn"] and report["status"] == "partial"
    # bn: every line was skipped after API errors, so the language must not report success.
    assert "none of 3 units could be dubbed" in report["languages"]["bn"]["error"]
    assert (out / "ta" / "segments.ta.json").exists()
    assert "simulated crash" in report["languages"]["ta"]["error"]

    srt = (out / "hi" / "subtitles.hi.srt").read_text(encoding="utf-8")
    assert "[AI-dubbed audio]" in srt and "आज हम कोणों" in srt
    seg = json.loads((out / "hi" / "segments.hi.json").read_text())["segments"][0]
    assert seg["cer"] == 0.0 and seg["voice"] in {"Kavya", "Amit"}


def test_run_fails_when_no_speech_could_be_transcribed(tmp_path, clip, fixed_speech):
    out = tmp_path / "out"
    opts = RunOptions(input=clip, langs=["hi"], out=out, cache_dir=out / ".cache")
    result = pipeline.run(opts, SETTINGS, client=FakeBodhan(deaf=True), llm=FakeLLM())
    report = json.loads((out / "report.json").read_text())
    assert result.failed == ["hi"] and report["status"] == "failed"
    assert "transcription failed for all 3 speech chunks" in report["error"]
    assert not (out / "hi" / "video.hi.mp4").exists()  # no fake "AI-dubbed" video


def test_video_without_speech_is_ok_and_report_is_strict_json(tmp_path, clip, monkeypatch):
    def no_speech(samples, params=None, backend="auto"):
        return vad.VadResult(regions=[], probs=np.zeros(10, np.float32), backend="stub",
                             duration_s=len(samples) / vad.SAMPLE_RATE)

    monkeypatch.setattr(pipeline.vad, "detect_speech", no_speech)
    out = tmp_path / "out"
    opts = RunOptions(input=clip, langs=["hi"], out=out, cache_dir=out / ".cache")
    result = pipeline.run(opts, SETTINGS, client=FakeBodhan(), llm=FakeLLM())

    def no_constants(name):
        raise ValueError(f"non-JSON constant {name}")

    report = json.loads((out / "report.json").read_text(), parse_constant=no_constants)
    hi = report["languages"]["hi"]
    assert result.failed == [] and hi["status"] == "ok"
    assert hi["speech_lufs"] is None and hi["misses"] == []
    assert abs(hi["total_length_ms"]) <= 100


@pytest.mark.live
def test_cli_live_30s(tmp_path):
    source = os.environ.get("BODHAN_DUB_SMOKE_VIDEO")
    if not source:
        pytest.skip("set BODHAN_DUB_SMOKE_VIDEO to an English video")
    clip = tmp_path / "clip30.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", "0", "-t", "30", "-i", source, "-c", "copy",
                    str(clip)], check=True)
    out = tmp_path / "out"
    cmd = [sys.executable, "-m", "bodhan_dub", "--input", str(clip), "--langs", "hi", "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads((out / "report.json").read_text())
    assert report["languages"]["hi"]["status"] == "ok"
    assert Path(out / "hi" / "video.hi.mp4").stat().st_size > 0


CONTAINERS = {
    "mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"],
    "mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libopus"],
    "webm": ["-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus"],
    "vp8.webm": ["-c:v", "libvpx", "-b:v", "200k", "-c:a", "libopus"],
}


@pytest.mark.parametrize("container", list(CONTAINERS))
def test_every_supported_container_dubs_to_the_exact_input_length(tmp_path, fixed_speech, container):
    src = tmp_path / f"in.{container}"
    made = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error",
                           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=30",
                           "-f", "lavfi", "-i", "sine=frequency=330:duration=30",
                           *CONTAINERS[container], "-shortest", str(src)], capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip(f"this ffmpeg build cannot write {container}: {made.stderr[-200:]}")
    out = tmp_path / "out"
    opts = RunOptions(input=src, langs=["hi"], out=out, cache_dir=out / ".cache")
    result = pipeline.run(opts, SETTINGS, client=FakeBodhan(), llm=FakeLLM())
    hi = json.loads((out / "report.json").read_text())["languages"]["hi"]
    assert result.failed == [] and hi["coverage"]["dubbed"] == 3
    assert abs(hi["total_length_ms"]) <= 100, hi["total_length_ms"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(out / "hi" / "video.hi.mp4")], capture_output=True, text=True).stdout.split()
    assert sorted(probe) == ["audio", "video"]
