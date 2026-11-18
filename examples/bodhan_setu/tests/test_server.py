import asyncio
import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

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
        "translate_text": TranslateTextResult,
        "transliterate_text": TransliterateTextResult,
        "synthesize_speech": SynthesizeSpeechResult,
        "ocr_document": OcrDocumentResult,
        "get_file": GetFileResult,
    }
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    for name, schema_type in expected.items():
        assert name in tools
        assert tools[name].fn_metadata.output_schema["title"] == schema_type.__name__
        assert tools[name].fn_metadata.output_schema["type"] == "object"

    protocol_tools = asyncio.run(server.mcp.list_tools())
    assert all(tool.outputSchema for tool in protocol_tools)

    content, structured = asyncio.run(
        server.mcp._tool_manager.call_tool(
            "translate_text",
            {"text": "hello " * 2_000, "target_language": "ta", "dry_run": True},
            convert_result=True,
        )
    )
    assert content
    assert structured["dry_run"] is True
    assert structured["chunk_count"] == 2
