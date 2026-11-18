#!/usr/bin/env python3
from __future__ import annotations

import io
import tempfile
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bodhan_mcp.tools import (
    inspect_media,
    transcribe_audio,
    extract_document_text,
    translate_text,
    synthesize_speech,
)
from bodhan_mcp.models import (
    SUPPORTED_TRANSCRIBE_LANGUAGES,
    SUPPORTED_TRANSLATE_LANGUAGES,
    SUPPORTED_TTS_LANGUAGES,
)
from bodhan_mcp.client import BodhanCache


def create_test_wav(path: str, duration_sec: float = 1.0, sample_rate: int = 16000):
    """Create a simple test WAV file."""
    n_frames = int(duration_sec * sample_rate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)


def create_test_wav_bytes(duration_sec: float = 0.5, sample_rate: int = 24000) -> bytes:
    """Create a simple test WAV file as bytes."""
    n_frames = int(duration_sec * sample_rate)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return buffer.getvalue()


class TestInspectMedia:
    @pytest.mark.asyncio
    async def test_inspect_audio_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            create_test_wav(str(wav_path), duration_sec=2.0)
            result = await inspect_media(str(wav_path))
            assert result["format"] == "wav"
            assert result["min_cost_inr"] >= 0
            assert result["supported_for_transcribe"] is True

    @pytest.mark.asyncio
    async def test_inspect_pdf_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"
            try:
                import pypdfium2
                doc = pypdfium2.PdfDocument.new()
                doc.new_page(width=612, height=792)
                doc.save(str(pdf_path))
                doc.close()
                result = await inspect_media(str(pdf_path))
                assert result["format"] == "pdf"
                assert result["pages"] == 1
                assert result["min_cost_inr"] > 0
                assert result["supported_for_ocr"] is True
            except ImportError:
                pytest.skip("pypdfium2 not available")

    @pytest.mark.asyncio
    async def test_inspect_text_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = Path(tmpdir) / "test.txt"
            txt_path.write_text("Hello world", encoding="utf-8")
            result = await inspect_media(str(txt_path))
            assert result["format"] == "txt"
            assert result["min_cost_inr"] == 0.0
            assert result["supported_for_translate"] is True

    @pytest.mark.asyncio
    async def test_inspect_missing_file(self):
        result = await inspect_media("/nonexistent/path.wav")
        assert "error" in result
        assert result["error"] == "FILE_NOT_FOUND"
        assert "File not found" in result["message"]
        assert result["retryable"] is False


class TestTranscribeAudio:
    @pytest.mark.asyncio
    async def test_transcribe_audio_missing_file(self):
        result = await transcribe_audio("/nonexistent/audio.wav")
        assert "error" in result
        assert result["error"] == "FILE_NOT_FOUND"
        assert "File not found" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_transcribe_audio_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            create_test_wav(str(wav_path), duration_sec=2.0)

            with patch("bodhan_mcp.tools.transcribe.client") as mock_client:
                mock_client.request = AsyncMock(return_value=(
                    {"text": "Hello world", "duration_sec": 2.0},
                    0.10
                ))
                result = await transcribe_audio(str(wav_path), language_hint="en")
                assert "text" in result
                assert "<untrusted_source_data>" in result["text"]
                assert result["cost_inr"] == 0.10
                assert "segments" in result

    @pytest.mark.asyncio
    async def test_transcribe_audio_probe_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            create_test_wav(str(wav_path), duration_sec=30.0)

            with patch("bodhan_mcp.tools.transcribe.client") as mock_client:
                mock_client.request = AsyncMock(return_value=(
                    {"text": "Short probe", "duration_sec": 15.0},
                    0.025
                ))
                result = await transcribe_audio(str(wav_path), max_duration_sec=15.0)
                assert result["duration_sec"] > 0
                mock_client.request.assert_called_once()


class TestExtractDocumentText:
    @pytest.mark.asyncio
    async def test_extract_document_text_missing_file(self):
        result = await extract_document_text("/nonexistent/doc.pdf")
        assert "error" in result
        assert result["error"] == "FILE_NOT_FOUND"
        assert "File not found" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_extract_document_text_unsupported_format(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = Path(tmpdir) / "test.txt"
            txt_path.write_text("test")
            result = await extract_document_text(str(txt_path))
            assert "error" in result
            assert result["error"] == "VALIDATION_ERROR"
            assert "Unsupported file format" in result["message"]
            assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_extract_document_text_page_range_parsing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"
            try:
                import pypdfium2
                doc = pypdfium2.PdfDocument.new()
                for _ in range(5):
                    doc.new_page(width=612, height=792)
                doc.save(str(pdf_path))
                doc.close()

                with patch("bodhan_mcp.tools.ocr.client") as mock_client:
                    mock_client.request = AsyncMock(return_value=(
                        {"choices": [{"message": {"content": "Page content"}}], "blocks": []},
                        0.20
                    ))
                    result = await extract_document_text(str(pdf_path), page_range="2-3")
                    assert result["pages"] == 2
            except ImportError:
                pytest.skip("pypdfium2 not available")


class TestTranslateText:
    @pytest.mark.asyncio
    async def test_translate_unsupported_source_language(self):
        result = await translate_text("fr", "hi", text="Hello")
        assert "error" in result
        assert result["error"] == "UNSUPPORTED_LANGUAGE"
        assert "fr" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_translate_unsupported_target_language(self):
        result = await translate_text("hi", "fr", text="\u0928\u092e\u0938\u094d\u0924\u0947")
        assert "error" in result
        assert result["error"] == "UNSUPPORTED_LANGUAGE"
        assert "fr" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_translate_invalid_script(self):
        result = await translate_text("hi", "ta", text="\u0928\u092e\u0938\u094d\u0924\u0947", script="invalid")
        assert "error" in result
        assert result["error"] == "VALIDATION_ERROR"
        assert "invalid" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_translate_missing_input(self):
        result = await translate_text("hi", "ta")
        assert "error" in result
        assert result["error"] == "VALIDATION_ERROR"
        assert "Either text or input_file" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_translate_empty_text(self):
        result = await translate_text("hi", "ta", text="")
        assert result["translation"] == "<untrusted_source_data></untrusted_source_data>"
        assert result["cost_inr"] == 0.0

    @pytest.mark.asyncio
    async def test_translate_success(self):
        with tempfile.TemporaryDirectory():
            with patch("bodhan_mcp.tools.translate.client") as mock_client:
                mock_client.request = AsyncMock(return_value=(
                    {"translation": "\u0935\u0923\u0915\u094d\u0915\u092e", "script": "native", "truncated": False},
                    0.02
                ))
                result = await translate_text("hi", "ta", text="\u0928\u092e\u0938\u094d\u0924\u0947")
                assert "<untrusted_source_data>\u0935\u0923\u0915\u094d\u0915\u092e</untrusted_source_data>" in result["translation"]
                assert result["target_language"] == "ta"
                assert result["cost_inr"] == 0.02
                assert "target_script_verified" in result

    @pytest.mark.asyncio
    async def test_translate_writes_output_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "output.txt"
            with patch("bodhan_mcp.tools.translate.client") as mock_client:
                mock_client.request = AsyncMock(return_value=(
                    {"translation": "Translated text", "script": "native", "truncated": False},
                    0.02
                ))
                await translate_text("hi", "ta", text="\u0928\u092e\u0938\u094d\u0924\u0947", output_file=str(out_file))
                assert out_file.exists()
                assert "Translated text" in out_file.read_text()


class TestSynthesizeSpeech:
    @pytest.mark.asyncio
    async def test_synthesize_unsupported_language(self):
        result = await synthesize_speech("fr", "output.wav", text="Hello")
        assert "error" in result
        assert result["error"] == "UNSUPPORTED_LANGUAGE"
        assert "fr" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_synthesize_unsupported_voice(self):
        result = await synthesize_speech("hi", "output.wav", text="\u0928\u092e\u0938\u094d\u0924\u0947", voice_id="NonExistentVoice")
        assert "error" in result
        assert result["error"] == "UNSUPPORTED_VOICE"
        assert "NonExistentVoice" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_synthesize_invalid_style(self):
        result = await synthesize_speech("hi", "output.wav", text="\u0928\u092e\u0938\u094d\u0924\u0947", style="invalid_style")
        assert "error" in result
        assert result["error"] == "VALIDATION_ERROR"
        assert "invalid_style" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_synthesize_missing_input(self):
        result = await synthesize_speech("hi", "output.wav")
        assert "error" in result
        assert result["error"] == "VALIDATION_ERROR"
        assert "Either text or input_file" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_synthesize_empty_text(self):
        result = await synthesize_speech("hi", "output.wav", text="")
        assert "error" in result
        assert result["error"] == "VALIDATION_ERROR"
        assert "Input text is empty" in result["message"]
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_synthesize_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "output.wav"
            wav_data = create_test_wav_bytes()
            with patch("bodhan_mcp.tools.speak.client") as mock_client:
                mock_client.request = AsyncMock(return_value=(wav_data, 0.60))
                result = await synthesize_speech("hi", str(out_file), text="\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e")
                assert out_file.exists()
                assert result["chunks_count"] >= 1
                assert result["cost_inr"] == 0.60
                assert result["language"] == "hi"
                assert result["voice_id"] in ["Kavya", "Suhani", "Amit"]

    @pytest.mark.asyncio
    async def test_synthesize_from_input_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            in_file = Path(tmpdir) / "input.txt"
            in_file.write_text("\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e", encoding="utf-8")
            out_file = Path(tmpdir) / "output.wav"
            wav_data = create_test_wav_bytes()
            with patch("bodhan_mcp.tools.speak.client") as mock_client:
                mock_client.request = AsyncMock(return_value=(wav_data, 0.60))
                result = await synthesize_speech("hi", str(out_file), input_file=str(in_file))
                assert out_file.exists()
                assert result["chunks_count"] >= 1


class TestCacheDeduplication:
    @pytest.mark.asyncio
    async def test_disk_cache_deduplication(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = BodhanCache(cache_dir=tmpdir)
            model = "test-model"
            endpoint = "/test"
            params = {"param": "value"}
            input_bytes = b"test input"
            result = {"data": "test result"}
            cost = 0.10

            cache.set(model, endpoint, params, input_bytes, result, cost)

            cached = cache.get(model, endpoint, params, input_bytes)
            assert cached is not None
            assert cached.response == result
            assert cached.cost_inr == cost

            cached2 = cache.get(model, endpoint, params, input_bytes)
            assert cached2 is not None
            assert cached2.response == result

    @pytest.mark.asyncio
    async def test_cache_different_inputs_not_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = BodhanCache(cache_dir=tmpdir)
            model = "test-model"
            endpoint = "/test"
            params = {"param": "value"}
            input_bytes1 = b"test input 1"
            input_bytes2 = b"test input 2"
            result1 = {"data": "result 1"}
            result2 = {"data": "result 2"}
            cost = 0.10

            cache.set(model, endpoint, params, input_bytes1, result1, cost)
            cache.set(model, endpoint, params, input_bytes2, result2, cost)

            cached1 = cache.get(model, endpoint, params, input_bytes1)
            cached2 = cache.get(model, endpoint, params, input_bytes2)

            assert cached1 is not None
            assert cached2 is not None
            assert cached1.response != cached2.response


class TestLanguageValidation:
    def test_supported_transcribe_languages(self):
        assert "hi" in SUPPORTED_TRANSCRIBE_LANGUAGES
        assert "ta" in SUPPORTED_TRANSCRIBE_LANGUAGES
        assert "en" in SUPPORTED_TRANSCRIBE_LANGUAGES
        assert "fr" not in SUPPORTED_TRANSCRIBE_LANGUAGES

    def test_supported_translate_languages(self):
        assert "hi" in SUPPORTED_TRANSLATE_LANGUAGES
        assert "ta" in SUPPORTED_TRANSLATE_LANGUAGES
        assert "en" in SUPPORTED_TRANSLATE_LANGUAGES
        assert "fr" not in SUPPORTED_TRANSLATE_LANGUAGES

    def test_supported_tts_languages(self):
        assert "hi" in SUPPORTED_TTS_LANGUAGES
        assert "ta" in SUPPORTED_TTS_LANGUAGES
        assert "en" in SUPPORTED_TTS_LANGUAGES
        assert "fr" not in SUPPORTED_TTS_LANGUAGES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
