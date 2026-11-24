import asyncio
import io
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import get_type_hints
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from bodhan_mcp import server
from bodhan_mcp.schemas import (
    ChunkAudioResult,
    ConvertPdfResult,
    ExtractVideoAudioResult,
    GetFileResult,
    OcrDocumentResult,
    ReplaceVideoAudioResult,
    SynthesizeSpeechResult,
    TranscribeAudioResult,
    TranscribeVideoResult,
    TranslateAudioResult,
    TranslateTextResult,
    TranslateVideoResult,
    TransliterateTextResult,
)
from bodhan_cache import CacheRecord, CacheStore, MemoryBackend, stable_key


def test_translate_text_rejects_empty_input():
    with pytest.raises(Exception, match="must not be empty"):
        server.translate_text("", "hi")


def test_cache_key_is_order_independent():
    first = stable_key("translate_text", "indic-translate", {"text": "hello", "target": "hi"})
    second = stable_key("translate_text", "indic-translate", {"target": "hi", "text": "hello"})
    assert first == second


def test_memory_cache_round_trip():
    cache = CacheStore(MemoryBackend())
    cache.set("key", CacheRecord(result={"value": 1}))
    assert cache.get("key").result == {"value": 1}


def _isolated_cache(tmp_path, monkeypatch):
    cache = CacheStore(MemoryBackend())
    monkeypatch.setattr(server, "get_cache", lambda: cache)
    monkeypatch.setattr(server, "ARTIFACT_DIR", tmp_path / "artifacts")
    return cache


def _wav_bytes(seconds=0.05, rate=24_000):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * int(seconds * rate))
    return stream.getvalue()


def _write_wav(path: Path, seconds: int, rate: int = 16_000):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * seconds * rate)


def _write_speech_wav(path: Path, seconds: int = 4, rate: int = 16_000):
    import math
    import struct

    frames = bytearray()
    for index in range(seconds * rate):
        moment = index / rate
        amplitude = 5000 if 1.0 <= moment < 2.5 else 100
        value = int(amplitude * math.sin(2 * math.pi * 220 * moment))
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(frames)


def test_translation_chunks_at_safe_boundaries_and_reassembles(monkeypatch, tmp_path):
    _isolated_cache(tmp_path, monkeypatch)
    calls = []

    def mock_request(method, url, key, **kwargs):
        part = kwargs["json"]["text"]
        calls.append(part)
        return SimpleNamespace(json=lambda: {"translation": part.upper(), "usage": {"completion_tokens": len(part)}})

    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server, "_request", mock_request)
    source = "Hello world. " * 900
    result = server.translate_text(source, "ta")

    assert len(calls) == 2
    assert all(len(part) <= server.MAX_TRANSLATE_CHARS_PER_REQUEST for part in calls)
    file_id = result["file_id"].removeprefix("setu:=")
    full_translation = Path(server.get_cache().get_artifact(file_id)["path"]).read_text()
    assert full_translation == source.strip().upper()
    assert result["translation_truncated"] is True
    assert result["chunk_count"] == result["translated_chunks"] == 2
    assert result["cost_inr"] > 0


def test_identical_translation_payload_hits_cache_but_target_language_misses(monkeypatch, tmp_path):
    cache = _isolated_cache(tmp_path, monkeypatch)
    calls = []

    def mock_request(method, url, key, **kwargs):
        calls.append(dict(kwargs["json"]))
        language = kwargs["json"]["target_language"]
        return SimpleNamespace(json=lambda: {"translation": f"{language}:ok", "usage": {"completion_tokens": 2}})

    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server, "_request", mock_request)
    first = server.translate_text("same source", "ta")
    cached_estimate = server.translate_text("same source", "ta", dry_run=True)
    repeated = server.translate_text("same source", "ta")
    other_language = server.translate_text("same source", "bn")

    assert len(calls) == 2
    assert first["cache_hit"] is False
    assert cached_estimate["cache_hit"] is True and cached_estimate["estimated_cost_inr"] == 0
    assert cached_estimate["will_call_api"] is False
    assert repeated["cache_hit"] is True and repeated["cost_inr"] == 0
    assert other_language["cache_hit"] is False
    assert len(cache.backend._items) == 2


def test_concurrent_identical_calls_are_singleflighted(monkeypatch, tmp_path):
    _isolated_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    calls = []

    def mock_request(method, url, key, **kwargs):
        calls.append(1)
        time.sleep(0.03)
        return SimpleNamespace(json=lambda: {"translation": "ok", "usage": {"completion_tokens": 2}})

    monkeypatch.setattr(server, "_request", mock_request)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _i: server.translate_text("one payload", "ta"), range(6)))
    assert len(calls) == 1
    assert sum(result["cache_hit"] is False for result in results) == 1
    assert all(result["translation"] == "ok" for result in results)


def test_cached_binary_result_has_zero_estimate_and_uses_new_output_path(monkeypatch, tmp_path):
    cache = _isolated_cache(tmp_path, monkeypatch)
    original = tmp_path / "model-cache.wav"
    original.write_bytes(_wav_bytes())
    key = stable_key("translate_audio", "bodhan-workflow", {"input_sha256": "same"})
    cache.set(key, CacheRecord(
        result={"output_file": "/old/run/output.wav", "target_language": "ta"},
        artifact_path=str(original), artifact_sha256=server.sha256_file(original), cost_inr=0.4,
    ))

    estimate = server._cached_estimate(key, 10, "characters")
    output = tmp_path / "new-run" / "translated.wav"
    result = server._cached_file_result(key, output, "audio/wav")
    assert estimate["estimated_cost_inr"] == 0
    assert estimate["cache_hit"] is True and estimate["will_call_api"] is False
    assert output.read_bytes() == original.read_bytes()
    assert result["output_file"] == str(output)
    assert result["cache_hit"] is True and result["cost_inr"] == 0


def test_cache_only_mode_blocks_cache_misses_before_network(monkeypatch, tmp_path):
    _isolated_cache(tmp_path, monkeypatch)
    monkeypatch.setenv("BODHAN_CACHE_ONLY", "true")
    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server.requests, "request", lambda *args, **kwargs: pytest.fail("network request attempted"))
    with pytest.raises(server.ToolError, match="BODHAN_CACHE_ONLY"):
        server.translate_text("cache miss", "ta")


def test_translation_dry_run_reports_internal_chunks_without_api(monkeypatch):
    monkeypatch.setattr(server, "_request", lambda *args, **kwargs: pytest.fail("dry run called API"))
    result = server.translate_text("word " * 10_200, "ta", dry_run=True)
    assert result["dry_run"] is True
    assert result["chunk_count"] == 7
    assert result["chunk_limit_chars"] == 8_000
    assert result["will_call_api"] is False


@pytest.mark.parametrize(
    "source",
    [
        "Sentence one. Sentence two? Final sentence!",
        "Heading\n\nParagraph one has several words.\n\nParagraph two.",
        "unbrokenwordwithoutspaces0123456789",
    ],
)
def test_translation_splitter_preserves_all_source_characters(source):
    chunks = server._translation_chunks(source, max_chars=12)
    assert all(0 < len(part) <= 12 for part, _ in chunks)
    assert "".join(part + separator for part, separator in chunks) == source.strip()


def test_translation_partial_failure_reports_progress_and_does_not_cache(monkeypatch, tmp_path):
    cache = _isolated_cache(tmp_path, monkeypatch)
    calls = 0

    def mock_request(method, url, key, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise server.ToolError("temporary upstream error")
        return SimpleNamespace(json=lambda: {"translation": "ok", "usage": {"completion_tokens": 2}})

    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server, "_request", mock_request)
    with pytest.raises(server.ToolError, match=r"chunk 2/2 after 1 chunk\(s\) completed"):
        server.translate_text("A. " * 5_000, "ta")
    assert not cache.backend._items


def test_transcribe_audio_splits_long_wav_and_joins_results(monkeypatch, tmp_path):
    _isolated_cache(tmp_path, monkeypatch)
    audio_path = tmp_path / "long.wav"
    _write_wav(audio_path, 53)
    calls = []

    def mock_request(method, url, key, **kwargs):
        calls.append(kwargs["files"]["file"][1].read())
        return SimpleNamespace(json=lambda: {"text": f"part{len(calls)}"})

    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server, "_request", mock_request)
    result = server.transcribe_audio(str(audio_path), language="hi")
    assert len(calls) == 3
    assert result["text"] == "part1 part2 part3"
    assert result["chunks"] == 3


def test_transcribe_audio_dry_run_reports_chunk_count(tmp_path):
    audio_path = tmp_path / "long.wav"
    _write_wav(audio_path, 53)
    result = server.transcribe_audio(str(audio_path), dry_run=True)
    assert result["chunk_count"] == 3
    assert result["will_call_api"] is False


def test_translate_audio_dry_run_detects_vad_without_model_calls(tmp_path, monkeypatch):
    source = tmp_path / "speech.wav"
    _write_speech_wav(source)
    monkeypatch.setattr(server, "transcribe_audio", lambda *args, **kwargs: pytest.fail("dry run called transcription"))
    monkeypatch.setattr(server, "translate_text", lambda *args, **kwargs: pytest.fail("dry run called translation"))
    monkeypatch.setattr(server, "synthesize_speech", lambda *args, **kwargs: pytest.fail("dry run called synthesis"))

    result = server._translate_audio_regions(
        source,
        tmp_path / "out.wav",
        target_language="hi",
        voice="Amit",
        source_language=None,
        script=None,
        output_tokens_estimate=None,
        speech_characters_estimate=None,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["speech_regions"] == 1
    assert result["estimate_complete"] is False
    assert result["will_call_api"] is False


def test_translate_audio_replaces_only_vad_spans_and_keeps_source_duration(tmp_path, monkeypatch):
    source = tmp_path / "speech.wav"
    output = tmp_path / "translated.wav"
    _write_speech_wav(source)

    def fake_transcribe(path, language=None):
        return {"text": "hello", "cost_inr": 0.01}

    def fake_translate(text, target, script=None):
        return {"translation": "namaste", "cost_inr": 0.02}

    def fake_synthesize(text, language, voice, path):
        _write_wav(Path(path), 1)
        return {"cost_inr": 0.03}

    def fake_fit(path, fitted, duration, rate):
        frame_count = round(duration * rate)
        with wave.open(str(fitted), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(b"\x10\x27" * frame_count)

    monkeypatch.setattr(server, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr(server, "translate_text", fake_translate)
    monkeypatch.setattr(server, "synthesize_speech", fake_synthesize)
    monkeypatch.setattr(server, "_fit_wav_duration", fake_fit)
    intervals = server._vad_intervals(source, max_seconds=25, min_silence_ms=300, padding_ms=120)[1]

    result = server._translate_audio_regions(
        source,
        output,
        target_language="hi",
        voice="Amit",
        source_language=None,
        script=None,
        output_tokens_estimate=5,
        speech_characters_estimate=10,
        dry_run=False,
    )

    with wave.open(str(source), "rb") as source_wav, wave.open(str(output), "rb") as output_wav:
        original = source_wav.readframes(source_wav.getnframes())
        translated = output_wav.readframes(output_wav.getnframes())
        assert output_wav.getnframes() == source_wav.getnframes()
    start, end = intervals[0]
    assert translated[: start * 2] == original[: start * 2]
    assert translated[end * 2 :] == original[end * 2 :]
    assert translated[start * 2 : end * 2] != original[start * 2 : end * 2]
    assert result["translated_regions"] == 1
    assert result["transcript"] == "hello"
    assert result["translation"] == "namaste"


@pytest.mark.skipif(not server.shutil.which("ffmpeg") or not server.shutil.which("ffprobe"), reason="FFmpeg is required")
def test_audio_duration_fit_and_video_mux_preserve_source_duration(tmp_path):
    speech = tmp_path / "speech.wav"
    fitted = tmp_path / "fitted.wav"
    _write_wav(speech, 2)
    server._fit_wav_duration(speech, fitted, 1.25, 16_000)
    assert server._wav_info(fitted)[2] == pytest.approx(1.25, abs=0.02)

    video = tmp_path / "source.mp4"
    replacement_audio = tmp_path / "replacement.wav"
    output = tmp_path / "translated.mp4"
    ffmpeg = server._find_media_binary("ffmpeg")
    server._run_media_command(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x120:r=10:d=1.6", "-t", "1.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        "Video fixture creation",
    )
    _write_wav(replacement_audio, 3)
    result = server._replace_video_audio(video, replacement_audio, output)
    assert result["duration_strategy"] == "source_duration_preserved_audio_trimmed"
    assert server._probe_video_duration(output) == pytest.approx(server._probe_video_duration(video), abs=0.1)


def test_chunk_audio_splits_to_requested_duration(monkeypatch, tmp_path):
    _isolated_cache(tmp_path, monkeypatch)
    source = tmp_path / "long.wav"
    _write_wav(source, 53)
    result = server.chunk_audio(str(source), str(tmp_path / "chunks"), max_chunk_seconds=25)
    assert result["chunk_count"] == 3
    assert [chunk["duration_seconds"] for chunk in result["chunks"]] == [25.0, 25.0, 3.0]


def test_synthesize_speech_splits_long_text_and_merges_wav(monkeypatch, tmp_path):
    _isolated_cache(tmp_path, monkeypatch)
    output = tmp_path / "speech.wav"
    requests = []

    def mock_request(method, url, key, **kwargs):
        requests.append(kwargs["json"]["input"])
        return SimpleNamespace(content=_wav_bytes())

    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server, "_request", mock_request)
    monkeypatch.setattr(server, "_persistent_model_artifact", lambda path: path)
    source = "A short sentence. " * 40
    result = server.synthesize_speech(source, "en", "Amit", str(output))
    assert len(requests) > 1
    assert all(0 < len(part) <= server.MAX_SPEAK_CHARS for part in requests)
    assert result["chunks"] == len(requests)
    with wave.open(str(output), "rb") as audio:
        assert audio.getnframes() > 0


def test_synthesize_speech_dry_run_reports_chunks(monkeypatch):
    monkeypatch.setattr(server, "_request", lambda *args, **kwargs: pytest.fail("dry run called API"))
    result = server.synthesize_speech("A sentence. " * 40, "en", "Amit", "unused.wav", dry_run=True)
    assert result["chunk_count"] > 1
    assert result["will_call_api"] is False


def test_ocr_document_handles_each_pdf_page(monkeypatch, tmp_path):
    fitz = pytest.importorskip("fitz")
    _isolated_cache(tmp_path, monkeypatch)
    pdf_path = tmp_path / "two-pages.pdf"
    doc = fitz.open()
    for text in ("first page", "second page"):
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(pdf_path)
    doc.close()
    calls = []

    def mock_request(method, url, key, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(json=lambda: {"choices": [{"message": {"content": f"ocr page {len(calls)}"}}]})

    monkeypatch.setattr(server, "_key", lambda name: "test-key")
    monkeypatch.setattr(server, "_request", mock_request)
    output = tmp_path / "ocr.md"
    result = server.ocr_document(str(pdf_path), str(output))
    assert len(calls) == 2
    assert result["pages"] == 2
    assert "## Page 1" in output.read_text()
    assert "## Page 2" in output.read_text()


def test_ocr_pdf_dry_run_counts_pages_without_api(monkeypatch, tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "two-pages.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()
    monkeypatch.setattr(server, "_request", lambda *args, **kwargs: pytest.fail("dry run called API"))
    result = server.ocr_document(str(pdf_path), "unused.md", dry_run=True)
    assert result["page_count"] == 2
    assert result["will_call_api"] is False


def test_pdf_conversion_renders_all_pages(monkeypatch, tmp_path):
    fitz = pytest.importorskip("fitz")
    _isolated_cache(tmp_path, monkeypatch)
    pdf_path = tmp_path / "two-pages.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()
    result = server.convert_pdf_to_images(str(pdf_path), str(tmp_path / "pages"))
    assert result["page_count"] == 2
    assert [page["page"] for page in result["pages"]] == [1, 2]
    assert all(Path(page["file_path"]).is_file() for page in result["pages"])


def test_pdf_and_audio_tools_reject_out_of_range_chunk_limits(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "one-page.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()
    with pytest.raises(server.ToolError, match="dpi must be between"):
        server.convert_pdf_to_images(str(pdf_path), str(tmp_path / "pages"), dpi=20)

    wav_path = tmp_path / "short.wav"
    _write_wav(wav_path, 1)
    with pytest.raises(server.ToolError, match="max_chunk_seconds must be between"):
        server.chunk_audio(str(wav_path), str(tmp_path / "chunks"), max_chunk_seconds=30)


def test_all_tools_have_typed_output_contracts():
    expected = {
        "convert_pdf_to_images": ConvertPdfResult,
        "chunk_audio": ChunkAudioResult,
        "transcribe_audio": TranscribeAudioResult,
        "transcribe_video": TranscribeVideoResult,
        "extract_video_audio": ExtractVideoAudioResult,
        "replace_video_audio_track": ReplaceVideoAudioResult,
        "translate_video": TranslateVideoResult,
        "translate_audio": TranslateAudioResult,
        "translate_text": TranslateTextResult,
        "transliterate_text": TransliterateTextResult,
        "synthesize_speech": SynthesizeSpeechResult,
        "ocr_document": OcrDocumentResult,
        "get_file": GetFileResult,
    }
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    protocol_tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name, schema_type in expected.items():
        assert name in tools
        assert get_type_hints(tools[name].fn)["return"] is schema_type
        wire_tool = protocol_tools[name].model_dump(by_alias=True)
        assert isinstance(wire_tool["outputSchema"], dict)
        assert wire_tool["outputSchema"]["type"] == "object"
    structured = server.translate_text("hello " * 2_000, "ta", dry_run=True)
    assert structured["dry_run"] is True
    assert structured["chunk_count"] == 2
    import jsonschema
    jsonschema.validate(structured, tools["translate_text"].output_schema)


def test_mcp_dry_run_structured_outputs_validate_including_nullable_fields(tmp_path):
    fitz = pytest.importorskip("fitz")
    wav_path = tmp_path / "short.wav"
    _write_wav(wav_path, seconds=1)
    image_path = tmp_path / "one-pixel.png"
    document = fitz.open()
    document.new_page(width=2, height=2).get_pixmap().save(image_path)
    document.close()
    calls = [
        ("transcribe_audio", {"audio_file": str(wav_path), "dry_run": True}),
        ("ocr_document", {"image_file": str(image_path), "output_file": str(tmp_path / "ocr.md"), "dry_run": True}),
        ("translate_text", {"text": "hello", "target_language": "ta", "dry_run": True}),
        ("synthesize_speech", {"text": "hello", "language": "en", "voice": "Amit", "output_file": str(tmp_path / "speech.wav"), "dry_run": True}),
    ]
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    import jsonschema
    for name, arguments in calls:
        result = asyncio.run(server.mcp.call_tool(name, arguments))
        assert isinstance(result, tuple) and isinstance(result[1], dict)
        jsonschema.validate(result[1], tools[name].outputSchema)


def test_bodhan_request_retries_rate_limit_with_bounded_backoff(monkeypatch):
    monkeypatch.setattr(server, "_MAX_BODHAN_RETRIES", 2)
    monkeypatch.setattr(server, "_BODHAN_REQUEST_LIMITER", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server.time, "sleep", lambda _delay: None)
    calls = []

    class Response:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}
            self.ok = status < 400
            self.text = ""
            self.closed = False

        def close(self):
            self.closed = True

    responses = [Response(429, {"Retry-After": "0"}), Response(200)]

    def fake_request(*_args, **_kwargs):
        calls.append(1)
        return responses.pop(0)

    monkeypatch.setattr(server.requests, "request", fake_request)
    response = server._request("POST", "https://bodhan.example", "test-key", json={"x": 1})
    assert response.status_code == 200
    assert len(calls) == 2


def test_bodhan_request_does_not_retry_permanent_budget_429(monkeypatch):
    monkeypatch.setattr(server, "_MAX_BODHAN_RETRIES", 2)
    monkeypatch.setattr(server, "_BODHAN_REQUEST_LIMITER", threading.BoundedSemaphore(1))
    calls = []

    class Response:
        status_code = 429
        headers = {"Retry-After": "0"}
        ok = False
        text = "ExceededBudget"

        def json(self):
            return {"error": {"code": "ExceededBudget", "message": "account budget exceeded"}}

        def close(self):
            pass

    monkeypatch.setattr(server.requests, "request", lambda *_args, **_kwargs: (calls.append(1), Response())[1])
    with pytest.raises(server.ToolError, match="ExceededBudget"):
        server._request("POST", "https://bodhan.example", "test-key")
    assert len(calls) == 1


def test_bodhan_request_retries_connect_timeout_but_not_ambiguous_read_timeout(monkeypatch):
    monkeypatch.setattr(server, "_MAX_BODHAN_RETRIES", 1)
    monkeypatch.setattr(server, "_BODHAN_REQUEST_LIMITER", threading.BoundedSemaphore(1))
    monkeypatch.setattr(server.time, "sleep", lambda _delay: None)

    class Response:
        status_code = 200
        headers = {}
        ok = True
        text = ""

        def close(self):
            pass

    attempts = iter([requests.ConnectTimeout("connect"), Response()])

    def fake_request(*_args, **_kwargs):
        result = next(attempts)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(server.requests, "request", fake_request)
    assert server._request("POST", "https://bodhan.example", "test-key").status_code == 200

    monkeypatch.setattr(server.requests, "request", lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.ReadTimeout("read")))
    with pytest.raises(server.ToolError, match="completion is uncertain"):
        server._request("POST", "https://bodhan.example", "test-key")


def test_bodhan_http_requests_respect_global_concurrency_limit(monkeypatch):
    monkeypatch.setattr(server, "_BODHAN_REQUEST_LIMITER", threading.BoundedSemaphore(2))
    monkeypatch.setattr(server, "_MAX_BODHAN_RETRIES", 0)
    active = 0
    maximum = 0
    lock = threading.Lock()

    class Response:
        status_code = 200
        headers = {}
        ok = True
        text = ""

        def close(self):
            pass

    def fake_request(*_args, **_kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return Response()

    monkeypatch.setattr(server.requests, "request", fake_request)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: server._request("POST", "https://bodhan.example", "test-key"), range(8)))
    assert maximum == 2
