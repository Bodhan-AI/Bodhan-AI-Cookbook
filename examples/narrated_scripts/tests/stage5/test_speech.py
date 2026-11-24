import io
import json
import wave

import httpx
import pytest
from PIL import Image

from backend import runner
from backend.audio.speak import HOSTED_VOICES, IndicSpeakProvider, synthesize_chunk, synthesize_script
from backend.inference import CUDA_OOM, Inference
from backend.models import NarrationScript, ScriptChunk, ScriptSection
from backend.report import AttemptLog
from backend.translation.provider import ModelTranslation
from bodhan_lesson import main


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 6000)
    return buffer.getvalue()


def script(*texts, voice=None, language="hi"):
    chunks = tuple(
        ScriptChunk(f"s001-c{index:02d}", "s001", ("p001-b001",), ("p001-b001",), text, "paragraph")
        for index, text in enumerate(texts, 1)
    )
    return NarrationScript(language, voice, 300, "completed", (ScriptSection("s001", chunks, ()),))


def unexpected(*args):
    pytest.fail(f"Unexpected synthesis call: {args}")


@pytest.mark.parametrize(("mode", "voice", "expected"), [
    ("hosted", None, "Kavya"),
    ("hosted", "Suhani", "Suhani"),
    ("local-first", None, "Kavya"),
    ("local-first", "Amit", "Amit"),
])
def test_resolves_voice_and_preserves_exact_input(tmp_path, mode, voice, expected):
    text = "  पहले पढ़ें।\nफिर सुनें।  "
    calls = []

    def hosted(request):
        assert request.url == httpx.URL("https://api.bodhan.ai/v1/audio/speech")
        assert request.headers["Authorization"] == "Bearer speech-test"
        payload = json.loads(request.content)
        assert payload["model"] == "indic-speak"
        assert json.loads(payload["instructions"]) == {"lang": "hi"}
        calls.append((payload["input"], payload["voice"]))
        return httpx.Response(200, content=wav_bytes(), headers={"Content-Type": "application/octet-stream"})

    def local(text, voice):
        calls.append((text, voice))
        return wav_bytes()

    with httpx.Client(transport=httpx.MockTransport(hosted if mode == "hosted" else unexpected)) as client:
        log = AttemptLog()
        provider = IndicSpeakProvider(mode, Inference(log), client, {"BODHAN_SPEAK_KEY": "speech-test"},
                                      local if mode == "local-first" else unexpected, unexpected)
        result = synthesize_script(script(text, voice=voice), tmp_path, provider)

    assert calls == [(text, expected)]
    assert result.voice == expected
    [candidate] = result.chunks
    assert candidate.requested_characters == len(text)
    assert candidate.attempt_id == log.attempts[0].id
    assert candidate.duration_seconds == 0.25
    assert candidate.format == "wav"
    assert candidate.audio_path.startswith("hi/work/audio/")
    assert (tmp_path / candidate.audio_path).read_bytes() == wav_bytes()


@pytest.mark.parametrize(("mode", "voice"), [("hosted", "Anitha"), ("local-first", "Suhani")])
def test_unsupported_target_voice_fails_before_synthesis(tmp_path, mode, voice):
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        log = AttemptLog()
        provider = IndicSpeakProvider(mode, Inference(log), client, {}, unexpected, unexpected)
        target = synthesize_script(script("पहला।", voice=voice), tmp_path, provider)

    assert target.status == "failed"
    assert voice in target.reason
    assert target.chunks[0].status == "failed"
    assert target.chunks[0].audio_path is None
    assert not log.attempts


def test_transient_retry_and_permanent_failures_preserve_other_chunks(tmp_path):
    texts = ("फिर कोशिश करें।", "पहुँच नहीं।", "अमान्य ऑडियो।", "अंत।")
    responses = iter((
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, content=wav_bytes()),
        httpx.Response(401, text="credential must not appear in attempt reason"),
        httpx.Response(200, content=wav_bytes()[:-2]),
        httpx.Response(200, content=wav_bytes()),
    ))
    calls = []
    waits = []

    def respond(request):
        calls.append(json.loads(request.content)["input"])
        return next(responses)

    log = AttemptLog()
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = IndicSpeakProvider("hosted", Inference(log, waits.append), client,
                                      {"BODHAN_SPEAK_KEY": "speech-test"}, unexpected, unexpected)
        target = synthesize_script(script(*texts), tmp_path, provider)

    assert calls == [texts[0], *texts]
    assert waits == [2]
    assert target.status == "completed_with_misses"
    first, denied, invalid, last = target.chunks
    assert first.status == last.status == "ok"
    for candidate in (first, last):
        with wave.open(str(tmp_path / candidate.audio_path)) as audio:
            assert audio.getnframes() == 6000
    assert denied.status == invalid.status == "failed"
    assert denied.reason == "Endpoint refused credentials (HTTP 401)."
    assert invalid.audio_path is None and "PCM WAV" in invalid.reason
    assert len(list((tmp_path / "hi" / "work" / "audio").iterdir())) == 2
    assert first.attempt_ids == tuple(item.id for item in log.attempts[:2])
    assert denied.attempt_ids == (log.attempts[2].id,)
    assert log.attempts[2].usage.cost_inr == 0
    assert log.attempts[1].usage.quantity == len(texts[0])
    assert log.attempts[1].usage.cost_inr == pytest.approx(len(texts[0]) * 6 / 10_000)
    assert log.attempts[1].usage.basis == "estimated"


@pytest.mark.parametrize("hosted_voice_available", [True, False])
def test_cuda_oom_fallback_preserves_text_and_voice(tmp_path, monkeypatch, hosted_voice_available):
    text = "  कक्षा शुरू होती है।  "
    calls = []

    if not hosted_voice_available:
        monkeypatch.setitem(HOSTED_VOICES, "hi", frozenset({"Kavya", "Suhani"}))

    def local(text, voice):
        calls.append(("local", text, voice))
        raise RuntimeError("CUDA out of memory during load")

    def hosted(request):
        data = json.loads(request.content)
        calls.append(("hosted", data["input"], data["voice"]))
        return httpx.Response(200, content=wav_bytes())

    local_releases = []
    log = AttemptLog()
    with httpx.Client(transport=httpx.MockTransport(hosted)) as client:
        provider = IndicSpeakProvider("local-first", Inference(log), client, {"BODHAN_SPEAK_KEY": "speech-test"},
                                      local, lambda: local_releases.append(True))
        target = synthesize_script(script(text, voice="Amit"), tmp_path, provider)

    assert local_releases == [True]
    assert [item.backend for item in log.attempts] == ["local", "hosted"]
    assert log.attempts[0].error_category == "cuda_oom"
    assert log.attempts[1].fallback_reason == CUDA_OOM
    [candidate] = target.chunks
    assert candidate.attempt_ids == tuple(item.id for item in log.attempts)
    assert calls[0] == ("local", text, "Amit")
    if hosted_voice_available:
        assert calls[1:] == [("hosted", text, "Amit")]
        assert candidate.status == "ok"
    else:
        assert len(calls) == 1
        assert candidate.status == "failed"
        assert "Voice 'Amit' is unavailable for hi on hosted" in candidate.reason
        assert log.attempts[1].usage.cost_inr == 0


def test_non_cuda_failure_does_not_fall_back(tmp_path):
    def local(text, voice):
        raise RuntimeError("Model weights missing")

    log = AttemptLog()
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        provider = IndicSpeakProvider("local-first", Inference(log), client, {}, local, unexpected)
        target = synthesize_script(script("कक्षा।"), tmp_path, provider)

    assert target.status == "failed"
    [attempt] = log.attempts
    assert attempt.backend == "local" and attempt.error_category == "local_failure"


def test_resynthesis_keeps_earlier_candidate(tmp_path):
    [chunk] = script("कक्षा।").sections[0].chunks
    paths = []
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        # Separate logs model reruns whose attempt IDs start at a000001 again.
        for _ in range(2):
            provider = IndicSpeakProvider("local-first", Inference(AttemptLog()), client, {},
                                          lambda text, voice: wav_bytes(), unexpected)
            paths.append(synthesize_chunk(chunk, "hi", "Kavya", tmp_path, provider).audio_path)
    assert paths[0] != paths[1]
    assert all((tmp_path / path).read_bytes() == wav_bytes() for path in paths)


def test_speech_cli_writes_indexes_for_failed_and_successful_targets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    for name in ("BODHAN_TRANSLATE_GGUF_URL", "BODHAN_SPEECH_MAX_CHARS",
                 "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "one.png")
    voices = tmp_path / "voices.yaml"
    voices.write_text("hi: Anitha\n", encoding="utf-8")
    monkeypatch.setattr(runner.LocalOCR, "__call__", lambda self, png, options: {
        "markdown": "# Geometry\n\nAn angle has two rays.",
    })
    monkeypatch.setattr(runner.LocalTranslate, "__call__",
                        lambda self, text, target: ModelTranslation("ज्यामिति।"))
    spoken = []

    def speak(self, text, voice):
        spoken.append((text, voice))
        return wav_bytes()

    monkeypatch.setattr(runner.LocalSpeak, "__call__", speak)
    out = tmp_path / "out"
    assert main(["--input", str(scans), "--out", str(out), "--langs", "hi,en", "--source-lang", "en",
                 "--voices", str(voices), "--until", "speech"]) == 1

    hi = json.loads((out / "hi" / "chunks.json").read_text(encoding="utf-8"))
    en = json.loads((out / "en" / "chunks.json").read_text(encoding="utf-8"))
    en_script = json.loads((out / "en" / "narration_script.en.json").read_text(encoding="utf-8"))
    assert hi["status"] == "failed" and hi["attempts"] == []
    assert "Anitha" in hi["reason"]
    assert en["status"] == "completed"
    assert spoken and spoken == [(chunk["text"], "Arun") for section in en_script["sections"]
                                 for chunk in section["chunks"]]
    for chunk, attempt in zip(en["chunks"], en["attempts"], strict=True):
        assert (out / chunk["audio_path"]).is_file()
        assert chunk["attempt_id"] == attempt["id"]
        assert attempt["backend"] == "local"
