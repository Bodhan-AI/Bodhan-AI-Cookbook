"""Recipe acceptance tests with simulated hosted responses and real media processing."""

from __future__ import annotations

import array
import io
import json
import math
import re
import runpy
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import wave
from collections import Counter
from dataclasses import dataclass, replace
from email import policy
from email.parser import BytesParser
from functools import cache
from pathlib import Path

import pytest

SAMPLE_RATE = 16_000
FRAME_SECONDS = 0.025
BACKGROUND_HZ = 440
FIXTURE_URL = "http://127.0.0.1:40404/v1"
AUTH = {
    "source": "Bearer placeholder-transcribe",
    "qa": "Bearer placeholder-transcribe",
    "translate": "Bearer placeholder-translate",
    "tts": "Bearer placeholder-speak",
    "judge": "Bearer placeholder-llm",
}
LANGUAGES = ("hi", "ta", "bn")


@dataclass(frozen=True)
class SpokenLine:
    start: float
    end: float
    marker: int
    english: str
    translations: dict[str, str]


LINES = (
    SpokenLine(2.0, 5.0, 200, "Angles A and B are equal, and they share a side.", {
        "hi": "कोण A और B बराबर हैं, और उनकी एक भुजा साझा है।",
        "ta": "A மற்றும் B கோணங்கள் சமம், மேலும் அவற்றுக்கு ஒரு பொதுப் பக்கம் உள்ளது.",
        "bn": "A ও B কোণ সমান, এবং তাদের একটি সাধারণ বাহু আছে।",
    }),
    SpokenLine(8.0, 11.0, 280, "The radius is five centimetres.", {
        "hi": "त्रिज्या पाँच सेंटीमीटर है।",
        "ta": "ஆரம் ஐந்து சென்டிமீட்டர்.",
        "bn": "ব্যাসার্ধ পাঁচ সেন্টিমিটার।",
    }),
    SpokenLine(16.0, 19.0, 360, "Draw the triangle on paper.", {
        "hi": "कागज़ पर त्रिभुज बनाएँ।",
        "ta": "காகிதத்தில் முக்கோணத்தை வரையுங்கள்.",
        "bn": "কাগজে ত্রিভুজটি আঁকো।",
    }),
)


def command(*args: str | Path) -> bytes:
    return subprocess.run(
        [str(arg) for arg in args], capture_output=True, check=True,
    ).stdout


def write_wave(samples: array.array) -> bytes:
    stream = io.BytesIO()
    if sys.byteorder == "big":
        samples = samples[:]
        samples.byteswap()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    return stream.getvalue()


def read_wave(body: bytes) -> tuple[array.array, int]:
    with wave.open(io.BytesIO(body), "rb") as source:
        assert source.getnchannels() == 1 and source.getsampwidth() == 2
        samples = array.array("h", source.readframes(source.getnframes()))
        if sys.byteorder == "big":
            samples.byteswap()
        return samples, source.getframerate()


@cache
def band_reference(frequency: int, rate: int) -> tuple[list[float], list[float]]:
    frame = round(rate * FRAME_SECONDS)
    phase = 2 * math.pi * frequency / rate
    return (
        [math.cos(index * phase) for index in range(frame)],
        [math.sin(index * phase) for index in range(frame)],
    )


def band_amplitudes(samples: array.array, rate: int, frequency: int) -> list[float]:
    cos_ref, sin_ref = band_reference(frequency, rate)
    frame = len(cos_ref)
    amplitudes = []
    for start in range(0, len(samples) - frame + 1, frame):
        window = samples[start:start + frame]
        real = sum(value * reference for value, reference in zip(window, cos_ref, strict=True))
        imaginary = sum(value * reference for value, reference in zip(window, sin_ref, strict=True))
        amplitudes.append(2 * math.hypot(real, imaginary) / (frame * 32768))
    return amplitudes


def acoustic_markers(
    samples: array.array, rate: int, frequencies: list[int], minimum_amplitude: float = 0.025,
) -> dict[int, tuple[float, float]]:
    markers = {}
    for frequency in frequencies:
        amplitudes = band_amplitudes(samples, rate, frequency)
        runs = []
        start = 0
        for end, value in enumerate([*amplitudes, -1.0]):
            if value < minimum_amplitude:
                if end - start >= 4:
                    runs.append((start, end))
                start = end + 1
        if runs:
            markers[frequency] = (runs[0][0] * FRAME_SECONDS, runs[-1][1] * FRAME_SECONDS)
    return markers


def create_video(
    path: Path, lines: tuple[SpokenLine, ...], background: bool = False,
    *, audible_cue: tuple[float, float, int, int] | None = None,
) -> Path:
    length = 61
    audio = array.array("h", [0]) * (length * SAMPLE_RATE)
    if background:
        start = 20 * SAMPLE_RATE
        end = 26 * SAMPLE_RATE
        audio[start:end] = array.array("h", (
            round(105 * math.sin(2 * math.pi * BACKGROUND_HZ * index / SAMPLE_RATE))
            for index in range(end - start)
        ))
    if audible_cue is not None:
        cue_start, cue_end, frequency, amplitude = audible_cue
        start, end = round(cue_start * SAMPLE_RATE), round(cue_end * SAMPLE_RATE)
        audio[start:end] = array.array("h", (
            round(amplitude * math.sin(2 * math.pi * frequency * index / SAMPLE_RATE))
            for index in range(end - start)
        ))
    for line in lines:
        start = round(line.start * SAMPLE_RATE)
        end = round(line.end * SAMPLE_RATE)
        samples = array.array("h")
        for sample in range(end - start):
            time = sample / SAMPLE_RATE
            phase = 2 * math.pi * line.marker * time
            attack = min(1, time / 0.02, (end - start - sample) / (SAMPLE_RATE * 0.02))
            syllables = 0.65 + 0.27 * math.sin(2 * math.pi * 4.7 * time)
            voice = math.sin(phase) + 0.45 * math.sin(2 * phase) + 0.20 * math.sin(3 * phase)
            samples.append(round(8000 * attack * syllables * voice))
        audio[start:end] = samples
    wav = path.with_suffix(".wav")
    wav.write_bytes(write_wave(audio))
    command(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "color=c=0x233447:s=96x64:r=10", "-i", wav,
        "-t", str(length), "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "64k", path,
    )
    return path


@cache
def synthesis_wave(frequency: int, duration: float) -> bytes:
    # A known 100 ms encoder lead followed by an independently detectable carrier.
    start = int(0.10 * SAMPLE_RATE)
    tone = array.array("h", (
        round(7000 * math.sin(2 * math.pi * frequency * index / SAMPLE_RATE))
        for index in range(round(duration * SAMPLE_RATE))
    ))
    return write_wave(array.array("h", [0]) * start + tone + array.array("h", [0]) * start)


def decoded_audio(path: Path, start: float = 0.0, duration: float | None = None) -> array.array:
    length = [] if duration is None else ["-t", str(duration)]
    raw = command(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(start), "-i", path,
        *length, "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
    )
    samples = array.array("h", raw)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def video_duration(path: Path) -> float:
    payload = command("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path)
    return float(json.loads(payload)["format"]["duration"])


def decoded_speech_lufs(path: Path, start: float) -> float:
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-i", str(path), "-t", "0.9",
        "-vn", "-af", "ebur128", "-f", "null", "-",
    ], capture_output=True, check=True, text=True)
    summary = result.stderr.rsplit("Summary:", 1)[-1]
    measurement = re.search(r"I:\s+(-?\d+\.\d+) LUFS", summary)
    assert measurement, summary
    return float(measurement.group(1))


@dataclass(frozen=True)
class Call:
    kind: str
    language: str
    text: str = ""
    duration: float = 0.0
    voice: str = ""


class FixtureResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}
        self.status = self.code = 200

    def getcode(self) -> int:
        return self.status


def fixture_chat(content: str, tokens: int = 12) -> FixtureResponse:
    return FixtureResponse(json.dumps({
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 30, "completion_tokens": tokens, "total_tokens": 30 + tokens},
    }, ensure_ascii=False).encode())


class SimulatedProvider:
    def __init__(self, lines: tuple[SpokenLine, ...], *, timestamps: bool = True) -> None:
        self.lines = lines
        self.timestamps = timestamps
        self.calls: list[Call] = []
        self.errors: list[str] = []
        self.lock = threading.Lock()
        self.attempts: Counter[tuple[str, str, str]] = Counter()
        self.persistent_failure = set()
        self.transient_failure = set()
        self.qa_override = {}
        self.extra_translations = {}
        self.fixtures = {}
        self.durations = {}
        self.judge = None
        self.interrupt = None
        for index, line in enumerate(lines):
            for language in (*LANGUAGES, "en"):
                text = line.english if language == "en" else line.translations[language]
                self.allow_synthesis(language, text, 640 + 80 * index)

    def allow_synthesis(self, language: str, text: str, frequency: int, duration: float = 1.4) -> None:
        self.fixtures[(language, text)] = frequency
        self.durations[(language, text)] = duration

    def log(self, request: urllib.request.Request, call: Call) -> int:
        headers = {name.lower(): value for name, value in request.header_items()}
        assert headers["authorization"] == AUTH[call.kind]
        with self.lock:
            self.calls.append(call)
            key = (call.kind, call.language, call.text)
            self.attempts[key] += 1
            return self.attempts[key]

    def open(self, request: urllib.request.Request) -> FixtureResponse:
        try:
            return self.dispatch(request)
        except (AssertionError, KeyError, ValueError) as error:
            with self.lock:
                self.errors.append(f"{request.full_url}: {error!r}")
            raise

    def dispatch(self, request: urllib.request.Request) -> FixtureResponse:
        assert request.full_url.startswith(f"{FIXTURE_URL}/"), request.full_url
        assert request.get_method() == "POST"
        route = request.full_url[len(FIXTURE_URL):]
        assert request.data is not None
        if route == "/audio/transcriptions":
            return self.transcribe(request)
        payload = json.loads(request.data)
        if route == "/chat/completions" and payload["model"] == "indic-translate":
            return self.translate(request, payload)
        if route == "/audio/speech":
            return self.speak(request, payload)
        assert route == "/chat/completions" and payload["model"] == "fixture-text-model", payload
        self.log(request, Call("judge", "", json.dumps(payload, ensure_ascii=False)))
        return fixture_chat(json.dumps(self.judge(payload) if self.judge else {}, ensure_ascii=False))

    def transcribe(self, request: urllib.request.Request) -> FixtureResponse:
        content_type = request.get_header("Content-type")
        assert content_type and content_type.startswith("multipart/form-data")
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + request.data,
        )
        fields = {
            part.get_param("name", header="Content-Disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()
        }
        assert fields["model"] == b"indic-transcribe"
        language = fields["language"].decode()
        samples, rate = read_wave(fields["file"])
        duration = len(samples) / rate
        assert 0 < duration <= 30
        source_markers = acoustic_markers(samples, rate, [line.marker for line in self.lines])
        if language == "en" and source_markers:
            spans = [
                {"start": source_markers[line.marker][0], "end": source_markers[line.marker][1],
                 "text": line.english}
                for line in self.lines if line.marker in source_markers
            ]
            text = " ".join(span["text"] for span in spans)
            self.log(request, Call("source", language, text, duration))
            response = {"text": text}
            if self.timestamps:
                response["segments"] = spans
        else:
            phrases = {marker: text for (lang, text), marker in self.fixtures.items() if lang == language}
            qa_markers = acoustic_markers(samples, rate, list(phrases), minimum_amplitude=0.035)
            assert qa_markers, f"verification received no test speech marker for {language}"
            present = [phrases[marker] for marker in sorted(qa_markers, key=lambda value: qa_markers[value][0])]
            actual = " ".join(self.qa_override.get((language, text), text) for text in present)
            self.log(request, Call("qa", language, " ".join(present), duration))
            response = {"text": actual}
        return FixtureResponse(json.dumps(response, ensure_ascii=False).encode())

    def translate(self, request: urllib.request.Request, payload: dict) -> FixtureResponse:
        assert payload["target_script"] == "native"
        language = payload["target_language_code"]
        assert language in LANGUAGES
        assert isinstance(payload["max_output_tokens"], int) and payload["max_output_tokens"] > 0
        assert len(payload["messages"]) == 1 and payload["messages"][0]["role"] == "user"
        source = payload["messages"][0]["content"]
        self.log(request, Call("translate", language, source))
        text = self.extra_translations.get((language, source))
        if text is None:
            lines = [line for line in self.lines if line.english in source]
            assert lines, f"unknown English translation input {source!r}"
            text = " ".join(line.translations[language] for line in lines)
        return fixture_chat(text)

    def speak(self, request: urllib.request.Request, payload: dict) -> FixtureResponse:
        assert payload["model"] == "indic-speak"
        language = json.loads(payload["instructions"])["lang"]
        text = payload["input"]
        assert isinstance(payload["voice"], str) and payload["voice"]
        assert payload["response_format"] == "wav"
        attempt = self.log(request, Call("tts", language, text, voice=payload["voice"]))
        if self.interrupt == (language, text):
            self.interrupt = None
            raise KeyboardInterrupt
        if language in self.persistent_failure or language in self.transient_failure and attempt == 1:
            error = json.dumps({"error": {"message": "simulated service unavailable"}}).encode()
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {"Retry-After": "0"}, io.BytesIO(error))
        marker = self.fixtures[(language, text)]
        duration = self.durations[(language, text)]
        return FixtureResponse(synthesis_wave(marker, duration), "audio/mpeg")

    def selected(self, kind: str, language: str | None = None) -> list[Call]:
        return [call for call in self.calls if call.kind == kind and (language is None or call.language == language)]


def invoke_cli(
    monkeypatch: pytest.MonkeyPatch, provider: SimulatedProvider, source: Path, out: Path,
    state: Path, *, languages: tuple[str, ...] = LANGUAGES, budget: str = "10",
    excerpt: int | None = None, workers: int = 3, use_judge: bool = False,
    extra_args: tuple[str, ...] = (), expect_interrupt: bool = False,
) -> int | None:
    with monkeypatch.context() as patch:
        # Exercise the actual __main__ module without requiring a listening socket.
        # The real client serializes every request before the fixture intercepts it.
        patch.setattr(urllib.request.OpenerDirector, "open", lambda _opener, request, *a, **kw: provider.open(request))
        for variable, value in {
            "BODHAN_BASE_URL": FIXTURE_URL, "BODHAN_TRANSCRIBE_KEY": "placeholder-transcribe",
            "BODHAN_TRANSLATE_KEY": "placeholder-translate", "BODHAN_SPEAK_KEY": "placeholder-speak",
            "LLM_BASE_URL": FIXTURE_URL if use_judge else "",
            "LLM_MODEL": "fixture-text-model" if use_judge else "",
            "LLM_API_KEY": "placeholder-llm" if use_judge else "",
        }.items():
            patch.setenv(variable, value)
        recipe = Path(__file__).resolve().parents[1]
        patch.syspath_prepend(str(recipe / "src"))
        args = [
            "bodhan_dub", "--input", str(source), "--langs", ",".join(languages), "--out", str(out),
            "--state-dir", str(state), "--budget-inr", budget, "--prior-spend-inr", "0", "--workers", str(workers),
        ]
        if excerpt is not None:
            args.extend(("--excerpt-seconds", str(excerpt)))
        args.extend(extra_args)
        patch.setattr(sys, "argv", args)
        with pytest.raises(KeyboardInterrupt if expect_interrupt else SystemExit) as result:
            runpy.run_module("bodhan_dub", run_name="__main__", alter_sys=True)
        assert not provider.errors, provider.errors
        return None if expect_interrupt else result.value.code


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cue_time(raw: str) -> float:
    hours, minutes, seconds, millis = (int(part) for part in re.split("[:,]", raw))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def read_cues(path: Path) -> list[tuple[float, float, str]]:
    cues = []
    for index, block in enumerate(path.read_text(encoding="utf-8").strip().split("\n\n"), 1):
        number, timing, text = block.split("\n", maxsplit=2)
        assert number == str(index)
        bounds = re.fullmatch(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", timing)
        assert bounds, timing
        start, end = map(cue_time, bounds.groups())
        assert start < end
        assert not cues or cues[-1][1] <= start
        cues.append((start, end, " ".join(text.splitlines())))
    return cues


def assert_media(out: Path, lang: str, provider: SimulatedProvider, expected_duration: float) -> None:
    folder = out / lang
    assert {path.name for path in folder.iterdir() if path.is_file()} == {
        f"video.{lang}.mp4", f"subtitles.{lang}.srt", f"segments.{lang}.json",
    }
    video = folder / f"video.{lang}.mp4"
    assert video_duration(video) == pytest.approx(expected_duration, abs=0.100)
    samples = decoded_audio(video)
    expected = [line.english if lang == "en" else line.translations[lang] for line in provider.lines]
    frequencies = [provider.fixtures[lang, text] for text in expected]
    placed = acoustic_markers(samples, SAMPLE_RATE, frequencies, minimum_amplitude=0.035)
    assert set(placed) == set(frequencies), placed
    cues = read_cues(folder / f"subtitles.{lang}.srt")
    assert [text for _, _, text in cues] == expected
    for line, frequency, (cue_start, cue_end, _) in zip(provider.lines, frequencies, cues, strict=True):
        audible_start, audible_end = placed[frequency]
        assert audible_start == pytest.approx(line.start, abs=0.300)
        assert cue_start == pytest.approx(audible_start, abs=0.170)
        assert cue_end == pytest.approx(audible_end, abs=0.170)
        assert audible_end <= line.end + 0.100
        assert cue_end <= expected_duration
    for first, second in zip(frequencies, frequencies[1:], strict=False):
        assert placed[first][1] < placed[second][0]


def test_three_language_excerpt_checks_rendered_speech_subtitles_and_qa(tmp_path, monkeypatch):
    video = create_video(tmp_path / "lesson.mp4", LINES, background=True)
    provider = SimulatedProvider(LINES)
    out = tmp_path / "out"
    assert invoke_cli(monkeypatch, provider, video, out, tmp_path / "state", excerpt=30) == 0
    assert len(provider.selected("source")) == len(LINES)
    assert [call.text for call in provider.selected("source")] == [line.english for line in LINES]
    assert {path.name for path in out.iterdir()} == {"report.json", "transcript.en.json", *LANGUAGES}
    report = load(out / "report.json")
    assert report["languages"].keys() == set(LANGUAGES)
    cost = report["cost"]["by_stage"]
    for stage in ("asr", "qa"):
        requests = provider.selected("source" if stage == "asr" else stage)
        assert cost[stage]["estimated_inr"] == pytest.approx(sum(call.duration for call in requests) / 600, abs=1e-8)
    assert cost["speak"]["estimated_inr"] == pytest.approx(sum(len(call.text) for call in provider.selected("tts")) * .0006)
    assert cost["translate"]["measured_usage_inr"] == pytest.approx(len(provider.selected("translate")) * 12 * .00002)
    transcript = load(out / "transcript.en.json")
    assert [segment["text"] for segment in transcript] == [line.english for line in LINES]
    for segment, line in zip(transcript, LINES, strict=True):
        assert segment["timing"] == "provider"
        assert segment["start"] == pytest.approx(line.start, abs=0.040)
        assert segment["end"] == pytest.approx(line.end, abs=0.040)
    for language in LANGUAGES:
        assert_media(out, language, provider, 30)
        assert [call.text for call in provider.selected("translate", language)] == [line.english for line in LINES]
        assert [call.text for call in provider.selected("tts", language)] == [line.translations[language] for line in LINES]
        assert [call.text for call in provider.selected("qa", language)] == [line.translations[language] for line in LINES]
        segments = load(out / language / f"segments.{language}.json")
        assert [segment["qa"]["cer"] for segment in segments] == [0, 0, 0]
        assert all(0.85 <= segment["tempo"] <= 1.20 for segment in segments)
        assert report["languages"][language]["coverage"] == 1
        source = decoded_audio(video, start=22, duration=2)
        output_video = out / language / f"video.{language}.mp4"
        dubbed = decoded_audio(output_video, start=22, duration=2)
        original_band = max(band_amplitudes(source, SAMPLE_RATE, BACKGROUND_HZ))
        dubbed_band = max(band_amplitudes(dubbed, SAMPLE_RATE, BACKGROUND_HZ))
        assert original_band > 0.001
        assert dubbed_band == pytest.approx(original_band, rel=0.30)
        assert decoded_speech_lufs(output_video, start=LINES[0].start + 0.15) == pytest.approx(-16, abs=1)


def test_plain_text_asr_and_configured_english_voice_with_normal_cli(tmp_path, monkeypatch):
    video = create_video(tmp_path / "lesson.mp4", LINES[:1])
    provider = SimulatedProvider(LINES[:1], timestamps=False)
    out = tmp_path / "out"
    voices = tmp_path / "voices.yaml"
    voices.write_text("en: Amit\n", encoding="utf-8")
    assert invoke_cli(
        monkeypatch, provider, video, out, tmp_path / "state", languages=("en",), extra_args=("--voices", str(voices)),
    ) == 0
    assert not provider.selected("translate")
    assert len(provider.selected("qa", "en")) == 1
    assert [call.voice for call in provider.selected("tts", "en")] == ["Amit"]
    assert load(out / "transcript.en.json")[0]["timing"] == "approximate"
    assert_media(out, "en", provider, video_duration(video))


def test_silent_video_has_no_hosted_calls_and_no_narration(tmp_path, monkeypatch):
    video = create_video(tmp_path / "silence.mp4", ())
    provider = SimulatedProvider(())
    out = tmp_path / "out"
    assert invoke_cli(monkeypatch, provider, video, out, tmp_path / "state", languages=("hi",)) == 0
    assert not provider.calls
    rendered = out / "hi" / "video.hi.mp4"
    assert video_duration(rendered) == pytest.approx(video_duration(video), abs=0.100)
    assert max(map(abs, decoded_audio(rendered))) <= 4
    assert (out / "hi" / "subtitles.hi.srt").read_text(encoding="utf-8").strip() == ""


def test_budget_shortfall_stops_all_languages_before_synthesis(tmp_path, monkeypatch):
    video = create_video(tmp_path / "lesson.mp4", LINES)
    provider = SimulatedProvider(LINES)
    out = tmp_path / "out"
    status = invoke_cli(monkeypatch, provider, video, out, tmp_path / "state", budget="0.065", excerpt=30)
    assert status != 0
    assert len(provider.selected("source")) == len(LINES)
    assert {call.language for call in provider.selected("translate")} == set(LANGUAGES)
    assert not provider.selected("tts")
    assert not provider.selected("qa")
    report = load(out / "report.json")
    assert report["preflight"]["status"] == "blocked"
    assert "budget" in json.dumps(report, ensure_ascii=False).lower()


def test_forced_asr_cut_deduplicates_timed_overlap(tmp_path, monkeypatch):
    lines = (
        replace(LINES[0], start=1.0, end=28.8),
        replace(LINES[1], start=28.8, end=31.2),
        replace(LINES[2], start=31.2, end=39.0),
    )
    video = create_video(tmp_path / "continuous.mp4", lines)
    provider = SimulatedProvider(lines)
    out = tmp_path / "out"
    assert invoke_cli(monkeypatch, provider, video, out, tmp_path / "state", languages=("en",)) == 0
    first, second = provider.selected("source")
    assert first.duration == pytest.approx(30, abs=0.025)
    assert second.duration == pytest.approx(8.5, abs=0.5)
    assert lines[1].english in first.text and lines[1].english in second.text
    transcript = load(out / "transcript.en.json")
    assert [segment["text"] for segment in transcript] == [line.english for line in lines]
    assert [segment["start"] for segment in transcript] == pytest.approx([line.start for line in lines], abs=0.040)
    assert transcript[1]["end"] == pytest.approx(lines[1].end, abs=0.040)
    assert len(transcript[1]["windows"]) == 2
    assert_media(out, "en", provider, video_duration(video))


def test_repetition_repair_checks_new_audio_and_reports_unrecoverable_omission(tmp_path, monkeypatch):
    video = create_video(tmp_path / "lesson.mp4", LINES)
    provider = SimulatedProvider(LINES)
    first = LINES[0].translations["hi"]
    third = LINES[2].translations["hi"]
    part_a = "कोण A और B बराबर हैं,"
    part_b = "और उनकी एक भुजा साझा है।"
    provider.extra_translations[("hi", "Angles A and B are equal,")] = part_a
    provider.extra_translations[("hi", "and they share a side.")] = part_b
    provider.allow_synthesis("hi", part_a, 960, duration=0.85)
    provider.allow_synthesis("hi", part_b, 1040, duration=0.85)
    provider.qa_override[("hi", first)] = first + " साझा है साझा है साझा है"
    provider.qa_override[("hi", third)] = "त्रिभुज"
    out = tmp_path / "out"
    assert invoke_cli(monkeypatch, provider, video, out, tmp_path / "state", languages=("hi",), excerpt=30) != 0
    syntheses = [call.text for call in provider.selected("tts", "hi")]
    assert Counter(syntheses) == Counter([first, LINES[1].translations["hi"], third, part_a, part_b])
    verified = [call.text for call in provider.selected("qa", "hi")]
    assert verified.count(first) == 2
    assert verified.count(third) == 1
    records = load(out / "hi" / "segments.hi.json")
    assert [attempt["action"] for attempt in records[0]["attempts"]] == ["baseline", "clause_split"]
    assert records[0]["qa"]["passed"] and records[0]["qa"]["cer"] == 0
    assert not records[2]["qa"]["passed"]
    assert records[2]["qa"]["cer"] > 0.38
    assert "possible_omission" in records[2]["qa"]["flags"]
    cues = read_cues(out / "hi" / "subtitles.hi.srt")
    assert [text for _, _, text in cues] == [first, LINES[1].translations["hi"]]
    audio = decoded_audio(out / "hi" / "video.hi.mp4")
    markers = acoustic_markers(audio, SAMPLE_RATE, [640, 720, 800, 960, 1040, LINES[2].marker])
    assert set(markers) == {720, 960, 1040, LINES[2].marker}
    assert markers[960][0] == pytest.approx(LINES[0].start, abs=0.300)
    assert markers[960][1] <= markers[1040][0]
    assert markers[1040][1] <= LINES[0].end
    assert markers[LINES[2].marker][0] == pytest.approx(LINES[2].start, abs=0.300)


def test_transient_and_persistent_language_failure_then_resume(tmp_path, monkeypatch):
    lines = LINES[:1]
    video = create_video(tmp_path / "lesson.mp4", lines)
    provider = SimulatedProvider(lines)
    provider.persistent_failure.add("ta")
    provider.transient_failure.add("hi")
    state = tmp_path / "state"
    out = tmp_path / "first"
    assert invoke_cli(monkeypatch, provider, video, out, state, excerpt=30) != 0
    assert len(provider.selected("tts", "hi")) == 2
    assert len(provider.selected("tts", "ta")) == 2
    assert len(provider.selected("tts", "bn")) == 1
    assert not provider.selected("qa", "ta")
    assert "503" in json.dumps(load(out / "ta" / "segments.ta.json"))
    assert not (out / "ta" / "video.ta.mp4").exists()
    initial = load(out / "report.json")
    assert initial["languages"]["ta"]["status"] == "failed"
    assert initial["cost"]["run"]["retries"] == 2
    for lang in ("hi", "bn"):
        assert_media(out, lang, provider, 30)

    resumed = SimulatedProvider(lines)
    next_out = tmp_path / "second"
    assert invoke_cli(monkeypatch, resumed, video, next_out, state, excerpt=30) == 0
    assert [(call.kind, call.language) for call in resumed.calls] == [("tts", "ta"), ("qa", "ta")]
    rerun = load(next_out / "report.json")
    assert rerun["cost"]["run"]["cache_hits"] == 8
    assert rerun["cost"]["lifetime"]["uncertain_inr"] == initial["cost"]["lifetime"]["uncertain_inr"]
    for lang in LANGUAGES:
        assert_media(next_out, lang, resumed, 30)


def test_pitch_preserving_fit_and_english_only_repair(tmp_path, monkeypatch):
    lines = LINES[:2]
    cue_start, cue_end, cue_hz = 11.45, 12.2, 1700
    video = create_video(tmp_path / "lesson.mp4", lines, audible_cue=(cue_start, cue_end, cue_hz, 500))
    provider = SimulatedProvider(lines)
    first, second = (line.translations["hi"] for line in lines)
    provider.allow_synthesis("hi", first, 640, duration=3.34)
    provider.allow_synthesis("hi", second, 720, duration=5.1)
    revised = "Radius: five centimetres."
    shortened = "त्रिज्या पाँच सेंटीमीटर।"
    provider.extra_translations[("hi", revised)] = shortened
    provider.allow_synthesis("hi", shortened, 880, duration=1.05)

    def decide(request):
        assert [message["role"] for message in request["messages"]] == ["system", "user"]
        assert json.loads(request["messages"][1]["content"])["source"] == lines[1].english
        return {"english": revised, "target_text": "never use this fixture bait"}

    provider.judge = decide
    out = tmp_path / "out"
    glossary = tmp_path / "glossary.yaml"
    glossary.write_text("radius:\n  hi: त्रिज्या\n", encoding="utf-8")
    assert invoke_cli(
        monkeypatch, provider, video, out, tmp_path / "state", languages=("hi",), excerpt=30, use_judge=True,
        extra_args=("--glossary", str(glossary)),
    ) == 0
    assert len(provider.selected("judge")) == 1
    assert [call.text for call in provider.selected("translate", "hi")] == [lines[0].english, lines[1].english, revised]
    assert [call.text for call in provider.selected("tts", "hi")] == [first, second, shortened]
    assert [call.text for call in provider.selected("qa", "hi")] == [first, shortened]
    records = load(out / "hi" / "segments.hi.json")
    assert 1.03 <= records[0]["tempo"] <= 1.20
    assert [attempt["action"] for attempt in records[1]["attempts"]] == ["baseline", "condense"]
    assert records[1]["qa"]["cer"] == 0
    assert records[1]["glossary"] == {"radius": "त्रिज्या"}
    audio = decoded_audio(out / "hi" / "video.hi.mp4")
    markers = acoustic_markers(audio, SAMPLE_RATE, [640, 720, 880])
    assert set(markers) == {640, 880}
    assert markers[640][0] == pytest.approx(lines[0].start, abs=0.300)
    assert markers[640][1] <= lines[0].end + 0.35
    assert markers[880][0] == pytest.approx(lines[1].start, abs=0.300)
    cue = acoustic_markers(audio, SAMPLE_RATE, [cue_hz], minimum_amplitude=0.006)
    assert cue[cue_hz] == pytest.approx((cue_start, cue_end), abs=0.050)


def test_interrupted_dispatch_keeps_reservation_and_reuses_completed_calls(tmp_path, monkeypatch):
    lines = LINES[:2]
    video = create_video(tmp_path / "lesson.mp4", lines)
    provider = SimulatedProvider(lines)
    first, second = (line.translations["hi"] for line in lines)
    provider.interrupt = ("hi", second)
    state = tmp_path / "state"
    out = tmp_path / "first"
    assert invoke_cli(
        monkeypatch, provider, video, out, state, languages=("hi",), excerpt=30, workers=1, expect_interrupt=True,
    ) is None
    assert [call.text for call in provider.selected("tts", "hi")] == [first, second]
    assert [call.text for call in provider.selected("qa", "hi")] == [first]
    interrupted = load(out / "report.json")["cost"]
    assert interrupted["lifetime"]["uncertain_inr"] == pytest.approx(len(second) * .0006)
    assert interrupted["held_inr"] == 0

    remaining = SimulatedProvider(lines)
    next_out = tmp_path / "second"
    assert invoke_cli(monkeypatch, remaining, video, next_out, state, languages=("hi",), excerpt=30) == 0
    assert [(call.kind, call.text) for call in remaining.calls] == [("tts", second), ("qa", second)]
    completed = load(next_out / "report.json")["cost"]
    assert completed["lifetime"]["uncertain_inr"] == interrupted["lifetime"]["uncertain_inr"]
    assert completed["lifetime"]["spent_inr"] + completed["lifetime"]["uncertain_inr"] < completed["cap_inr"]
    assert_media(next_out, "hi", remaining, 30)


def test_existing_bundle_survives_same_output_preflight_and_failed_resume(tmp_path, monkeypatch):
    lines = LINES[:1]
    video = create_video(tmp_path / "lesson.mp4", lines)
    out = tmp_path / "out"
    state = tmp_path / "state"
    assert invoke_cli(monkeypatch, SimulatedProvider(lines), video, out, state, languages=("hi",), excerpt=30) == 0
    bundle = {path: path.read_bytes() for path in (out / "hi").iterdir()}
    assert {path.name for path in bundle} == {"video.hi.mp4", "subtitles.hi.srt", "segments.hi.json"}

    preflight = SimulatedProvider(lines)
    assert invoke_cli(
        monkeypatch, preflight, video, out, state, languages=("hi",), excerpt=30, extra_args=("--preflight-only",),
    ) == 0
    assert not preflight.calls
    assert {path: path.read_bytes() for path in bundle} == bundle
    plan = load(out / "report.json")["languages"]["hi"]
    assert plan["retained_previous_bundle"]
    checkpoint = out / plan["segment_records"]
    assert checkpoint.name == ".checkpoint.hi.json"
    assert load(checkpoint)[0]["status"] == "pending"

    # Changing the voice forces a new TTS request while reusing source and translation.
    voices = tmp_path / "new_voice.yaml"
    voices.write_text("hi: Suhani\n", encoding="utf-8")
    voice_arg = ("--voices", str(voices))
    blocked = SimulatedProvider(lines)
    assert invoke_cli(
        monkeypatch, blocked, video, out, state, languages=("hi",), excerpt=30, budget="0.050", extra_args=voice_arg,
    ) != 0
    assert not blocked.calls
    assert {path: path.read_bytes() for path in bundle} == bundle
    cutoff = load(out / "report.json")
    assert cutoff["preflight"]["status"] == "blocked"
    assert cutoff["languages"]["hi"]["retained_previous_bundle"]
    assert "available" in load(out / cutoff["languages"]["hi"]["segment_records"])[0]["reason"]

    failure = SimulatedProvider(lines)
    failure.persistent_failure.add("hi")
    assert invoke_cli(
        monkeypatch, failure, video, out, state, languages=("hi",), excerpt=30, extra_args=voice_arg,
    ) != 0
    assert [(call.kind, call.voice) for call in failure.calls] == [("tts", "Suhani"), ("tts", "Suhani")]
    assert {path: path.read_bytes() for path in bundle} == bundle
    failed = load(out / "report.json")["languages"]["hi"]
    assert failed["status"] == "failed" and failed["retained_previous_bundle"]
    failed_segment = load(out / failed["segment_records"])[0]
    assert failed_segment["status"] == "failed" and "503" in failed_segment["reason"]
