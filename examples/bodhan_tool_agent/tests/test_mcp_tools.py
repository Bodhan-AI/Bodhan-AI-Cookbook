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
from bodhan_mcp.client import BodhanCache, APIResponse


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
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"text": "Hello world", "duration_sec": 2.0},
                    cost_inr=0.10, cached=False
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
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"text": "Short probe", "duration_sec": 15.0},
                    cost_inr=0.025, cached=False
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
            exe_path = Path(tmpdir) / "test.exe"
            exe_path.write_text("test")
            result = await extract_document_text(str(exe_path))
            assert "error" in result
            assert result["error"] == "VALIDATION_ERROR"
            assert "Unsupported file format" in result["message"]
            assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_extract_document_text_txt_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            txt_path = Path(tmpdir) / "test.txt"
            txt_path.write_text("Hello world")
            result = await extract_document_text(str(txt_path))
            assert "error" not in result
            assert result["text"] == "Hello world"
            assert result["full_text_file"].endswith(".txt")
            assert Path(result["full_text_file"]).exists()

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
                    mock_client.request = AsyncMock(return_value=APIResponse(
                        data={"choices": [{"message": {"content": "Page content"}}], "blocks": []},
                        cost_inr=0.20, cached=False
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
        assert "error" in result
        assert result["error"] == "EMPTY_INPUT"
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_translate_success(self):
        with tempfile.TemporaryDirectory():
            with patch("bodhan_mcp.tools.translate.client") as mock_client:
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"choices": [{"message": {"content": "\u0935\u0923\u0915\u094d\u0915\u092e"}}]},
                    cost_inr=0.02, cached=False
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
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"choices": [{"message": {"content": "Translated text"}}]},
                    cost_inr=0.02, cached=False
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
        assert result["error"] == "INVALID_VOICE"
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
        assert result["error"] == "EMPTY_INPUT"
        assert "empty" in result["message"].lower()
        assert result["retryable"] is False

    @pytest.mark.asyncio
    async def test_synthesize_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "output.wav"
            wav_data = create_test_wav_bytes()
            with patch("bodhan_mcp.tools.speak.client") as mock_client:
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data=wav_data, cost_inr=0.60, cached=False
                ))
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
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data=wav_data, cost_inr=0.60, cached=False
                ))
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


class TestLanguageNameNormalization:
    def test_normalize_english_name_to_code(self):
        from bodhan_mcp.tools._lang import normalize_lang
        assert normalize_lang("Hindi") == "hi"
        assert normalize_lang("English") == "en"
        assert normalize_lang("Tamil") == "ta"
        assert normalize_lang("Telugu") == "te"
        assert normalize_lang("Marathi") == "mr"
        assert normalize_lang("Bengali") == "bn"

    def test_normalize_lowercase_name_to_code(self):
        from bodhan_mcp.tools._lang import normalize_lang
        assert normalize_lang("hindi") == "hi"
        assert normalize_lang("tamil") == "ta"
        assert normalize_lang("bengali") == "bn"

    def test_iso_code_passes_through(self):
        from bodhan_mcp.tools._lang import normalize_lang
        assert normalize_lang("hi") == "hi"
        assert normalize_lang("ta") == "ta"
        assert normalize_lang("mr") == "mr"

    def test_unsupported_language_rejected(self):
        from bodhan_mcp.tools._lang import validate_lang
        code, err = validate_lang("French", "translate")
        assert err is not None
        assert err["error"] == "UNSUPPORTED_LANGUAGE"
        code, err = validate_lang("fr", "translate")
        assert err is not None
        assert err["error"] == "UNSUPPORTED_LANGUAGE"

    @pytest.mark.asyncio
    async def test_translate_accepts_name(self):
        with tempfile.TemporaryDirectory():
            with patch("bodhan_mcp.tools.translate.client") as mock_client:
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"choices": [{"message": {"content": "வணக்கம்"}}]},
                    cost_inr=0.02, cached=False
                ))
                result = await translate_text("English", "Tamil", text="Hello")
                assert result["target_language"] == "ta"

    @pytest.mark.asyncio
    async def test_tts_accepts_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "output.wav"
            wav_data = create_test_wav_bytes()
            with patch("bodhan_mcp.tools.speak.client") as mock_client:
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data=wav_data, cost_inr=0.60, cached=False
                ))
                result = await synthesize_speech("Hindi", str(out_file), text="नमस्ते दुनिया")
                assert result["language"] == "hi"

    @pytest.mark.asyncio
    async def test_transcribe_accepts_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            create_test_wav(str(wav_path), duration_sec=2.0)
            with patch("bodhan_mcp.tools.transcribe.client") as mock_client:
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"text": "Hello", "duration_sec": 2.0},
                    cost_inr=0.10, cached=False
                ))
                result = await transcribe_audio(str(wav_path), language_hint="English")
                assert result["language"] == "en"


class TestExtractDocumentTextReturnsTextAndFilePath:
    """Regression: extract_document_text must return usable text preview,
    full_text_file path, total_chars, and detected_script."""

    @pytest.mark.asyncio
    async def test_returns_text_preview_and_full_text_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"
            try:
                import pypdfium2
                doc = pypdfium2.PdfDocument.new()
                for _ in range(2):
                    doc.new_page(width=612, height=792)
                doc.save(str(pdf_path))
                doc.close()

                sample_content = "This is sample OCR content for testing. " * 20
                with patch("bodhan_mcp.tools.ocr.client") as mock_client:
                    mock_client.request = AsyncMock(return_value=APIResponse(
                        data={"choices": [{"message": {"content": sample_content}}], "blocks": []},
                        cost_inr=0.40, cached=False
                    ))
                    result = await extract_document_text(str(pdf_path))

                assert "error" not in result
                assert "text" in result, f"Missing 'text' key; keys={list(result.keys())}"
                assert "full_text_file" in result
                assert result["full_text_file"].endswith(".txt")
                assert Path(result["full_text_file"]).exists()
                assert result["total_chars"] > 0
                assert result["detected_script"] == "latin"
                # Preview should be wrapped in untrusted tags (via 'markdown' key).
                assert "<untrusted_source_data>" in result["markdown"]
                assert "</untrusted_source_data>" in result["markdown"]
            except ImportError:
                pytest.skip("pypdfium2 not available")

    @pytest.mark.asyncio
    async def test_long_text_is_bounded_to_2500_char_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "test.pdf"
            try:
                import pypdfium2
                doc = pypdfium2.PdfDocument.new()
                doc.new_page(width=612, height=792)
                doc.save(str(pdf_path))
                doc.close()

                long_content = "A" * 5000
                with patch("bodhan_mcp.tools.ocr.client") as mock_client:
                    mock_client.request = AsyncMock(return_value=APIResponse(
                        data={"choices": [{"message": {"content": long_content}}], "blocks": []},
                        cost_inr=0.20, cached=False
                    ))
                    result = await extract_document_text(str(pdf_path))

                import re
                match = re.search(r"<untrusted_source_data>(.*?)</untrusted_source_data>",
                                  result["markdown"], re.DOTALL)
                assert match, "Expected untrusted_source_data wrapper"
                preview = match.group(1)
                # Preview content (before truncation marker) must be <= 2500 chars.
                assert len(preview.split("\n\n...[truncated")[0]) <= 2500, \
                    f"Preview content too long: {len(preview)} chars"
                # Full text file should contain all of it.
                full_path = Path(result["full_text_file"])
                assert full_path.exists()
                assert len(full_path.read_text(encoding="utf-8")) == 5000
            except ImportError:
                pytest.skip("pypdfium2 not available")


class TestTranscribeAudioReturnsTextAndFilePath:
    """Regression: transcribe_audio must return usable text preview,
    full_text_file path, total_chars, and detected_language."""

    @pytest.mark.asyncio
    async def test_returns_text_and_full_text_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "test.wav"
            create_test_wav(str(wav_path), duration_sec=2.0)
            with patch("bodhan_mcp.tools.transcribe.client") as mock_client:
                mock_client.request = AsyncMock(return_value=APIResponse(
                    data={"text": "Hello world this is a transcription",
                          "duration_sec": 2.0, "segments": []},
                    cost_inr=0.10, cached=False
                ))
                result = await transcribe_audio(str(wav_path))
            assert "error" not in result
            assert "text" in result
            assert "full_text_file" in result
            assert result["full_text_file"].endswith(".txt")
            assert Path(result["full_text_file"]).exists()
            assert result["total_chars"] > 0
            assert "detected_language" in result


class TestCostAccounting:
    """Phase-1 regression for Defect A: translate usage keys and transcribe duration."""

    @pytest.mark.asyncio
    async def test_translate_with_completion_tokens_returns_nonzero_cost(self):
        """Live Bodhan API returns completion_tokens in usage, not output_tokens."""
        from bodhan_mcp.client import BodhanClient, InrLedger
        client = BodhanClient(ledger=InrLedger())
        result = {
            "translation": "வணககம்",
            "usage": {"completion_tokens": 16, "prompt_tokens": 2, "total_tokens": 18},
        }
        cost = client._calc_cost("indic-translate", None, None, result)
        assert cost > 0, f"Expected nonzero cost, got {cost}"

    @pytest.mark.asyncio
    async def test_translate_falls_back_to_output_tokens(self):
        """Older schema key 'output_tokens' still works when completion_tokens absent."""
        from bodhan_mcp.client import BodhanClient, InrLedger
        client = BodhanClient(ledger=InrLedger())
        result = {"translation": "hi", "usage": {"output_tokens": 10}}
        cost = client._calc_cost("indic-translate", None, None, result)
        assert cost > 0, f"Expected nonzero cost, got {cost}"

    @pytest.mark.asyncio
    async def test_translate_empty_usage_returns_minimum_floor(self):
        """When usage is missing/empty, _calc_cost charges at least the floor (>0)."""
        from bodhan_mcp.client import BodhanClient, InrLedger
        client = BodhanClient(ledger=InrLedger())
        # Empty usage dict — should hit the floor path.
        result = {"translation": "fallback text"}
        cost = client._calc_cost("indic-translate", None, None, result)
        assert cost >= 0.0001, f"Expected at least floor cost, got {cost}"

    @pytest.mark.asyncio
    async def test_translate_no_result_returns_zero(self):
        """With no result dict at all, cost is zero (no charge for errors)."""
        from bodhan_mcp.client import BodhanClient, InrLedger
        client = BodhanClient(ledger=InrLedger())
        cost = client._calc_cost("indic-translate", None, None, None)
        assert cost == 0.0

    @pytest.mark.asyncio
    async def test_transcribe_nonzero_for_real_duration(self):
        """Transcribe of N seconds bills proportionally, never zero for real audio."""
        from bodhan_mcp.client import BodhanClient, InrLedger
        client = BodhanClient(ledger=InrLedger())
        result = {"text": "hello", "duration_sec": 120.0, "segments": []}
        cost = client._calc_cost("indic-transcribe", None, None, result)
        assert cost > 0, f"Expected nonzero cost for 120s audio, got {cost}"
        assert cost == pytest.approx((120.0 / 60.0) * 0.10, abs=0.001)

    @pytest.mark.asyncio
    async def test_transcribe_zero_duration_yields_floor(self):
        """Transcribe with duration 0 still returns the default minute rate."""
        from bodhan_mcp.client import BodhanClient, InrLedger
        client = BodhanClient(ledger=InrLedger())
        result = {"text": "", "duration_sec": 0.0, "segments": []}
        cost = client._calc_cost("indic-transcribe", None, None, result)
        assert cost > 0, f"Expected default-minute cost, got {cost}"


class TestTranslateRateLimitBoundedRetries:
    """Regression: translate_text must honour bounded 429 retries and
    surface a structured RATE_LIMITED error rather than hanging."""

    @pytest.mark.asyncio
    async def test_rate_limited_after_bounded_retries_fails_fast(self):
        with patch("bodhan_mcp.tools.translate.client") as mock_client:
            from bodhan_mcp.client import BodhanError
            from bodhan_mcp.models import ErrorCode
            call_count = [0]
            async def side_effect(*args, **kwargs):
                call_count[0] += 1
                raise BodhanError(
                    "Rate limited", code=ErrorCode.RATE_LIMITED, retryable=True
                )
            mock_client.request = AsyncMock(side_effect=side_effect)
            result = await translate_text("en", "ta", text="Hello world")
            assert "error" in result
            assert result["error"] == "RATE_LIMITED", (
                f"Expected RATE_LIMITED, got {result.get('error')}; "
                f"details={result.get('details')}"
            )
            assert result["retryable"] is True
            # Should have made bounded retries (initial + 3 retries = 4 calls).
            assert call_count[0] == 4

    @pytest.mark.asyncio
    async def test_success_after_initial_429(self):
        with patch("bodhan_mcp.tools.translate.client") as mock_client:
            from bodhan_mcp.client import BodhanError
            from bodhan_mcp.models import ErrorCode
            call_count = [0]
            async def side_effect(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise BodhanError(
                        "Rate limited", code=ErrorCode.RATE_LIMITED, retryable=True
                    )
                return APIResponse(
                    data={"choices": [{"message": {"content": "வணக்கம்"}}]},
                    cost_inr=0.02, cached=False
                )
            mock_client.request = AsyncMock(side_effect=side_effect)
            result = await translate_text("en", "ta", text="Hello world")
            assert "error" not in result
            assert "வணக்கம்" in result["translation"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
