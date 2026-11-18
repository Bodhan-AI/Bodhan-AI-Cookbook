from __future__ import annotations

import base64
import io
import shutil
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from bodhan_agent import bodhan
from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings


@pytest.fixture
def settings():
    return Settings("transcribe-key", "translate-key", "speak-key", "ocr-key", "", "", "")


def _write_wav(path: Path, duration_seconds: int) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000 * duration_seconds)
    return path


def _wav_bytes(frame_count: int = 240):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * frame_count)
    return stream.getvalue()


def _completion(content="translated", *, finish_reason="stop", tokens=12, extras=None):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)],
        usage=SimpleNamespace(completion_tokens=tokens),
    )
    if extras is not None:
        response.model_dump = lambda: extras
    return response


def test_translate_text_chunks_and_uses_compatibility_fields(monkeypatch, settings):
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _completion(f"translation-{len(calls)}")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)
    text = "A" * bodhan.MAX_TRANSLATE_CHARS + "\n\n" + "B" * 200

    result = bodhan.translate_text(text, "ta", settings, script="roman")

    assert len(calls) == 2
    assert [call["messages"][0]["content"] for call in calls] == bodhan._translation_chunks(text)
    assert all(call["model"] == "indic-translate" for call in calls)
    assert all(call["extra_body"] == {"target_language_code": "ta", "script": "roman"} for call in calls)
    assert result["translation"] == "translation-1\n\ntranslation-2"
    assert result["chunks"] == 2
    assert result["output_tokens"] == 24
    assert len(result["verification_pairs"]) == 2


def test_translate_number_check_normalizes_indic_digits(monkeypatch, settings):
    outputs = iter(["१० AM, २ पिशव्या", "११ AM, २ पिशव्या"])
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: _completion(next(outputs)))
        )
    )
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)

    equivalent = bodhan.translate_text("10 AM, 2 bags", "mr", settings)
    changed = bodhan.translate_text("10 AM, 2 bags", "mr", settings)

    assert equivalent["verification"]["checks"]["numbers_preserved"] is True
    assert changed["verification"]["checks"]["numbers_preserved"] is False


def test_translate_checks_html_table_shape_and_indic_digits(monkeypatch, settings):
    source = "<table><tr><td>2 bags</td></tr></table>"
    translation = "<table><tr><td>२ पिशव्या</td></tr></table>"
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: _completion(translation))
        )
    )
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)

    result = bodhan.translate_text(source, "mr", settings)

    assert result["verification"]["checks"]["numbers_preserved"] is True
    assert result["verification"]["checks"]["html_table_structure_preserved"] is True


def test_translate_number_check_accepts_noon_as_twelve(monkeypatch, settings):
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: _completion("१० AM से दोपहर १२ बजे तक")
            )
        )
    )
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)

    result = bodhan.translate_text("10 AM to noon", "hi", settings)

    assert result["verification"]["checks"]["numbers_preserved"] is True


@pytest.mark.parametrize(
    ("text", "target", "script", "message"),
    [
        ("", "ta", "native", "text must not be empty"),
        ("hello", "xx", "native", "Unsupported language"),
        ("hello", "ta", "bad", "script must be"),
        ("hello", "en", "roman", "does not accept roman"),
        ("hello", "en", "codemix", "does not support codemix"),
    ],
)
def test_translate_text_rejects_invalid_inputs_before_api(monkeypatch, settings, text, target, script, message):
    monkeypatch.setattr(bodhan, "_client", lambda *args: pytest.fail("API client must not be created"))
    with pytest.raises(ToolError, match=message):
        bodhan.translate_text(text, target, settings, script=script)


@pytest.mark.parametrize(
    ("finish_reason", "extras"),
    [("length", {}), ("stop", {"truncated": True})],
)
def test_translate_text_rejects_truncated_response(monkeypatch, settings, finish_reason, extras):
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kwargs: _completion("partial", finish_reason=finish_reason, extras=extras)
        ))
    )
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)
    with pytest.raises(ToolError, match="Translation was truncated"):
        bodhan.translate_text("A" * 20, "ta", settings)


def test_ocr_image_encodes_png_and_returns_layout(monkeypatch, settings, tmp_path):
    image = tmp_path / "page.png"
    image.write_bytes(b"png-content")
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _completion("Extracted text", extras={"blocks": [{"text": "Extracted text"}]})

    monkeypatch.setattr(bodhan, "_client", lambda key, config: (calls.append({"key": key}) or SimpleNamespace(chat=SimpleNamespace(completions=Completions()))))
    result = bodhan.ocr_image(str(image), settings)

    request = calls[1]
    data_url = request["messages"][0]["content"][0]["image_url"]["url"]
    assert calls[0]["key"] == "ocr-key"
    assert request["model"] == "indic-ocr"
    assert data_url == "data:image/png;base64," + base64.b64encode(b"png-content").decode("ascii")
    assert result["text"] == "Extracted text"
    assert result["blocks"] == [{"text": "Extracted text"}]
    assert result["pages"] == 1
    assert result["cost_inr"] == bodhan.PRICES["ocr_per_page"]


def test_ocr_image_rejects_unsupported_file_and_empty_response(monkeypatch, settings, tmp_path):
    bad_file = tmp_path / "page.webp"
    bad_file.write_bytes(b"data")
    monkeypatch.setattr(bodhan, "_client", lambda *args: pytest.fail("unsupported image should fail before API"))
    with pytest.raises(ToolError, match="accepts PNG or JPEG"):
        bodhan.ocr_image(str(bad_file), settings)

    png = tmp_path / "empty.png"
    png.write_bytes(b"data")
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: _completion("  "))))
    monkeypatch.setattr(bodhan, "_client", lambda *args: fake_client)
    with pytest.raises(ToolError, match="returned no text"):
        bodhan.ocr_image(str(png), settings)


def test_ocr_folder_natural_sorts_pages_and_sums_cost(monkeypatch, settings, tmp_path):
    folder = tmp_path / "pages"
    folder.mkdir()
    for name in ("page-10.png", "page-2.jpg", "page-1.png"):
        (folder / name).write_bytes(b"x")
    seen = []

    def fake_ocr(path, config):
        seen.append(Path(path).name)
        return {"text": Path(path).stem, "blocks": [], "artifact_ref": path, "pages": 1, "cost_inr": 0.2}

    monkeypatch.setattr(bodhan, "ocr_image", fake_ocr)
    result = bodhan.ocr_document(str(folder), settings)

    assert seen == ["page-1.png", "page-2.jpg", "page-10.png"]
    assert result["pages"] == 3
    assert result["cost_inr"] == pytest.approx(0.6)
    assert result["text"].count("<!-- Page") == 3


def test_ocr_folder_rejects_empty_supported_page_set(settings, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "notes.txt").write_text("not an image")
    with pytest.raises(ToolError, match="contains no supported page images"):
        bodhan.ocr_document(str(folder), settings)


def test_ocr_pdf_rejects_missing_poppler_and_page_limit(monkeypatch, settings, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"pdf")
    monkeypatch.setattr(bodhan, "shutil_which", lambda command: None)
    with pytest.raises(ToolError, match="requires Poppler"):
        bodhan.ocr_document(str(pdf), settings)

    monkeypatch.setattr(bodhan, "shutil_which", lambda command: "mock-tool")
    monkeypatch.setattr(bodhan.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="Pages: 201\n"))
    with pytest.raises(ToolError, match="limit is 200 pages"):
        bodhan.ocr_document(str(pdf), settings)


def test_ocr_pdf_renders_and_ocr_sorts_all_pages(monkeypatch, settings, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"pdf")
    monkeypatch.setattr(bodhan, "shutil_which", lambda command: "mock-tool")

    def fake_run(command, **kwargs):
        if command[0] == "pdfinfo":
            return SimpleNamespace(stdout="Pages: 2\n")
        prefix = Path(command[-1])
        (prefix.parent / "page-2.png").write_bytes(b"page2")
        (prefix.parent / "page-1.png").write_bytes(b"page1")
        return SimpleNamespace(stdout="")

    seen = []

    def fake_ocr(path, config):
        seen.append(Path(path).read_bytes())
        return {"text": seen[-1].decode(), "blocks": [], "artifact_ref": path, "pages": 1, "cost_inr": 0.2}

    monkeypatch.setattr(bodhan.subprocess, "run", fake_run)
    monkeypatch.setattr(bodhan, "ocr_image", fake_ocr)
    result = bodhan.ocr_document(str(pdf), settings)

    assert seen == [b"page1", b"page2"]
    assert result["pages"] == 2
    assert result["cost_inr"] == pytest.approx(0.4)


def test_audio_chunks_passes_short_wav_through_without_ffmpeg(monkeypatch, tmp_path):
    short = _write_wav(tmp_path / "short.wav", 30)
    monkeypatch.setattr(bodhan, "shutil_which", lambda command: pytest.fail("short audio needs no ffmpeg"))
    assert bodhan._audio_chunks(short) == [(short, 0.0)]


def test_audio_chunks_splits_long_wav_into_bounded_windows(monkeypatch, tmp_path):
    source = _write_wav(tmp_path / "long.wav", 61)
    monkeypatch.setattr(bodhan, "shutil_which", lambda command: pytest.fail("PCM WAV splitting should not need ffmpeg"))
    chunks = bodhan._audio_chunks(source)
    try:
        assert [offset for _, offset in chunks] == [0.0, 27.0, 54.0]
        assert [bodhan._duration_seconds(path) for path, _ in chunks] == [27.0, 27.0, 7.0]
    finally:
        for path, _ in chunks:
            shutil.rmtree(path.parent, ignore_errors=True)


def test_audio_chunks_reports_missing_ffmpeg_for_long_non_wav(monkeypatch, tmp_path):
    source = tmp_path / "long.mp3"
    source.write_bytes(b"not-real-mp3")
    real_duration = bodhan._duration_seconds
    monkeypatch.setattr(bodhan, "_duration_seconds", lambda path: 61.0 if path == source else real_duration(path))
    monkeypatch.setattr(bodhan, "shutil_which", lambda command: None)
    with pytest.raises(ToolError, match="exceeds 30 seconds"):
        bodhan._audio_chunks(source)


def test_audio_chunks_uses_ffmpeg_for_long_non_wav(monkeypatch, tmp_path):
    source = tmp_path / "long.mp3"
    source.write_bytes(b"fake-mp3")
    real_duration = bodhan._duration_seconds
    monkeypatch.setattr(bodhan, "_duration_seconds", lambda path: 61.0 if path == source else real_duration(path))
    monkeypatch.setattr(bodhan, "shutil_which", lambda command: "mock-ffmpeg")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        duration = round(float(command[command.index("-t") + 1]))
        _write_wav(Path(command[-1]), duration)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bodhan.subprocess, "run", fake_run)
    chunks = bodhan._audio_chunks(source)
    try:
        assert [offset for _, offset in chunks] == [0.0, 27.0, 54.0]
        assert [bodhan._duration_seconds(path) for path, _ in chunks] == [27.0, 27.0, 7.0]
        assert len(commands) == 3
    finally:
        for path, _ in chunks:
            shutil.rmtree(path.parent, ignore_errors=True)


def test_transcribe_audio_estimates_segments_and_caches(monkeypatch, settings, tmp_path):
    audio_path = _write_wav(tmp_path / "speech.wav", 2)
    monkeypatch.setattr(bodhan, "_TRANSCRIBE_CACHE", {})
    calls = []

    class Transcriptions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="First sentence. Second sentence.")

    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=Transcriptions()))
    monkeypatch.setattr(bodhan, "_client", lambda key, config: (calls.append({"key": key}) or fake_client))
    result = bodhan.transcribe_audio(str(audio_path), settings, language="hi")
    cached = bodhan.transcribe_audio(str(audio_path), settings, language="hi")

    assert calls[0] == {"key": "transcribe-key"}
    assert calls[1]["model"] == "indic-transcribe"
    assert calls[1]["language"] == "hi"
    assert result["text"] == "First sentence. Second sentence."
    assert [segment["timing_estimated"] for segment in result["segments"]] == [True, True]
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][-1]["end"] == 2.0
    assert result["cost_inr"] == pytest.approx(2 / 60 * bodhan.PRICES["transcribe_per_minute"])
    assert cached["cached"] is True
    assert cached["cost_inr"] == 0.0
    assert len(calls) == 2


def test_transcribe_audio_preserves_real_api_timestamps(monkeypatch, settings, tmp_path):
    audio_path = _write_wav(tmp_path / "speech.wav", 2)
    monkeypatch.setattr(bodhan, "_TRANSCRIBE_CACHE", {})
    payload = {"text": "Hello.", "segments": [{"start": 0.25, "end": 1.5, "text": "Hello."}]}
    response = SimpleNamespace(model_dump=lambda: payload)
    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)

    result = bodhan.transcribe_audio(str(audio_path), settings)

    assert result["segments"] == [{"start": 0.25, "end": 1.5, "text": "Hello.", "timing_estimated": False}]


def test_transcribe_audio_rejects_unsupported_language_before_api(monkeypatch, settings, tmp_path):
    audio_path = _write_wav(tmp_path / "speech.wav", 1)
    monkeypatch.setattr(bodhan, "_client", lambda *args: pytest.fail("invalid language must fail before API"))
    with pytest.raises(ToolError, match="Unsupported language"):
        bodhan.transcribe_audio(str(audio_path), settings, language="xx")


def test_transcribe_audio_transcribes_each_chunk_with_global_offsets(monkeypatch, settings, tmp_path):
    source = _write_wav(tmp_path / "source.wav", 31)
    chunk1 = _write_wav(tmp_path / "part-1.wav", 27)
    chunk2 = _write_wav(tmp_path / "part-2.wav", 4)
    monkeypatch.setattr(bodhan, "_TRANSCRIBE_CACHE", {})
    monkeypatch.setattr(bodhan, "_audio_chunks", lambda path: [(chunk1, 0.0), (chunk2, 27.0)])
    responses = iter([SimpleNamespace(text="First."), SimpleNamespace(text="Second.")])
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return next(responses)

    fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)
    result = bodhan.transcribe_audio(str(source), settings, language="ta")

    assert len(calls) == 2
    assert result["text"] == "First. Second."
    assert result["segments"][0]["start"] == 0.0
    assert result["segments"][0]["end"] == 27.0
    assert result["segments"][1]["start"] == 27.0
    assert result["segments"][1]["end"] == 31.0
    assert all(segment["timing_estimated"] for segment in result["segments"])


def test_split_text_handles_empty_and_unbroken_input():
    with pytest.raises(ToolError, match="text must not be empty"):
        bodhan._split_text("  \n")
    text = "x" * (bodhan.MAX_TTS_CHARS + 50)
    chunks = bodhan._split_text(text)
    assert all(len(chunk) <= bodhan.MAX_TTS_CHARS for chunk in chunks)
    assert "".join(chunks) == text


def test_synthesize_speech_writes_wav_and_checks_it_with_transcription(monkeypatch, settings, tmp_path):
    calls = []

    class Speech:
        def create(self, **kwargs):
            calls.append(kwargs)
            audio = _wav_bytes()
            return SimpleNamespace(read=lambda: audio)

    fake_client = SimpleNamespace(audio=SimpleNamespace(speech=Speech()))
    monkeypatch.setattr(bodhan, "_client", lambda key, config: (calls.append({"key": key}) or fake_client))
    monkeypatch.setattr(bodhan, "transcribe_audio", lambda path, config, language=None: {"text": "வணக்கம்.", "cost_inr": 0.01})
    output = tmp_path / "artifacts" / "speech.wav"

    result = bodhan.synthesize_speech("வணக்கம்.", "ta", "Anitha", str(output), settings)

    assert bodhan._duration_seconds(output) == pytest.approx(0.01)
    assert calls[0] == {"key": "speak-key"}
    assert calls[1]["model"] == "indic-speak"
    assert calls[1]["voice"] == "Anitha"
    assert calls[1]["instructions"] == '{"lang": "ta"}'
    assert result["ai_generated"] is True
    assert result["verification"]["passed"] is True
    assert result["cost_inr"] == pytest.approx(0.01 + len("வணக்கம்.") / 10000 * bodhan.PRICES["speak_per_10k_chars"])


def test_synthesize_speech_chunks_joins_and_backchecks(monkeypatch, settings, tmp_path):
    text = ("தமிழ் வணக்கம். " * 30).strip()
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        audio = _wav_bytes()
        return SimpleNamespace(read=lambda: audio)

    fake_client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))
    monkeypatch.setattr(bodhan, "_client", lambda key, config: fake_client)
    monkeypatch.setattr(bodhan, "transcribe_audio", lambda path, config, language=None: {"text": text, "cost_inr": 0.02})
    output = tmp_path / "speech.wav"
    result = bodhan.synthesize_speech(text, "ta", "Anitha", str(output), settings)

    assert len(calls) > 1
    assert all(len(call["input"]) <= bodhan.MAX_TTS_CHARS for call in calls)
    assert bodhan._duration_seconds(output) == pytest.approx(len(calls) * 0.01)
    assert result["chunks"] == len(calls)
    assert result["verification"]["character_error_rate"] == 0.0


@pytest.mark.parametrize(("language", "voice"), [("xx", "Anitha"), ("ta", "NotARealVoice")])
def test_synthesize_speech_rejects_unsupported_language_or_voice(monkeypatch, settings, tmp_path, language, voice):
    monkeypatch.setattr(bodhan, "_client", lambda *args: pytest.fail("invalid speech request must fail before API"))
    with pytest.raises(ToolError):
        bodhan.synthesize_speech("hello", language, voice, str(tmp_path / "out.wav"), settings)
