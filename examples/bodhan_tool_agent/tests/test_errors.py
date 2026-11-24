"""Comprehensive error suite verifying structured error payloads on bad inputs across all tools (Slice 8)."""

from pathlib import Path

import pytest

from bodhan_mcp import config, errors
from bodhan_mcp.tools.media import chunk_audio, get_voice_library, inspect_media
from bodhan_mcp.tools.ocr import ocr_document, ocr_page
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_text


@pytest.fixture
def corrupted_file(tmp_path: Path) -> Path:
    """Create a non-empty corrupted file that cannot be decoded as valid audio or PDF."""
    corrupt = tmp_path / "corrupt.bin"
    corrupt.write_bytes(b"\x00\xff\xfe\x01\x10\x99GARBAGE_PAYLOAD")
    return corrupt


@pytest.mark.asyncio
async def test_transcribe_audio_error_paths(tmp_path: Path, corrupted_file: Path, short_wav: Path):
    """Verify transcribe_audio returns structured ToolErrorPayload on all invalid inputs."""
    # 1. FILE_NOT_FOUND
    res1 = await transcribe_audio(str(tmp_path / "does_not_exist.wav"))
    assert isinstance(res1, errors.ToolErrorPayload)
    assert res1.code == errors.FILE_NOT_FOUND
    assert "not found" in res1.message.lower()

    # 2. UNREADABLE_MEDIA
    res2 = await transcribe_audio(str(corrupted_file))
    assert isinstance(res2, errors.ToolErrorPayload)
    assert res2.code == errors.UNREADABLE_MEDIA

    # 3. UNSUPPORTED_LANGUAGE
    res3 = await transcribe_audio(str(short_wav), language_hint="japanese")
    assert isinstance(res3, errors.ToolErrorPayload)
    assert res3.code == errors.UNSUPPORTED_LANGUAGE


@pytest.mark.asyncio
async def test_translate_text_error_paths():
    """Verify translate_text returns structured errors on unsupported languages and missing args."""
    # 1. Unsupported source language
    res1 = await translate_text(text="Hello", source_lang="latin", target_lang="hi")
    assert isinstance(res1, errors.ToolErrorPayload)
    assert res1.code == errors.UNSUPPORTED_LANGUAGE

    # 2. Unsupported target language
    res2 = await translate_text(text="Hello", source_lang="en", target_lang="klingon")
    assert isinstance(res2, errors.ToolErrorPayload)
    assert res2.code == errors.UNSUPPORTED_LANGUAGE

    # 3. Missing argument / empty language
    res3 = await translate_text(text="Hello", source_lang="", target_lang="hi")
    assert isinstance(res3, errors.ToolErrorPayload)
    assert res3.code == errors.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_synthesize_speech_error_paths():
    """Verify synthesize_speech error paths: language, voice, and character caps."""
    # 1. UNSUPPORTED_LANGUAGE (English excluded for TTS per Rule F6)
    res1 = await synthesize_speech(text="Welcome", language="en", voice_id="any")
    assert isinstance(res1, errors.ToolErrorPayload)
    assert res1.code == errors.UNSUPPORTED_LANGUAGE

    # 2. INVALID_ARGUMENT (Voice not in library for language)
    res2 = await synthesize_speech(text="नमस्ते", language="hi", voice_id="invalid_voice_id")
    assert isinstance(res2, errors.ToolErrorPayload)
    assert res2.code == errors.INVALID_ARGUMENT

    # 3. INPUT_TOO_LONG (Exceeds 5000 chars)
    oversized_text = "नमस्कार " * 1000
    res3 = await synthesize_speech(text=oversized_text, language="hi", voice_id="hi_female_1")
    assert isinstance(res3, errors.ToolErrorPayload)
    assert res3.code == errors.INPUT_TOO_LONG
    assert "estimated_cost_inr" in res3.details


@pytest.mark.asyncio
async def test_ocr_tools_error_paths(tmp_path: Path, corrupted_file: Path):
    """Verify ocr_page and ocr_document return structured error payloads."""
    # ocr_page: FILE_NOT_FOUND
    res_page_missing = await ocr_page(str(tmp_path / "missing_img.png"))
    assert isinstance(res_page_missing, errors.ToolErrorPayload)
    assert res_page_missing.code == errors.FILE_NOT_FOUND

    # ocr_document: FILE_NOT_FOUND
    res_doc_missing = await ocr_document(str(tmp_path / "missing_doc.pdf"))
    assert isinstance(res_doc_missing, errors.ToolErrorPayload)
    assert res_doc_missing.code == errors.FILE_NOT_FOUND

    # ocr_document: UNREADABLE_MEDIA
    res_doc_corrupt = await ocr_document(str(corrupted_file))
    assert isinstance(res_doc_corrupt, errors.ToolErrorPayload)
    assert res_doc_corrupt.code == errors.UNREADABLE_MEDIA


def test_helper_tools_error_paths(tmp_path: Path, corrupted_file: Path):
    """Verify inspect_media and chunk_audio error handling."""
    # inspect_media: FILE_NOT_FOUND
    res_inspect_missing = inspect_media(str(tmp_path / "absent.wav"))
    assert isinstance(res_inspect_missing, errors.ToolErrorPayload)
    assert res_inspect_missing.code == errors.FILE_NOT_FOUND

    # chunk_audio: FILE_NOT_FOUND
    res_chunk_missing = chunk_audio(str(tmp_path / "absent.wav"))
    assert isinstance(res_chunk_missing, errors.ToolErrorPayload)
    assert res_chunk_missing.code == errors.FILE_NOT_FOUND

    # chunk_audio: UNREADABLE_MEDIA
    res_chunk_corrupt = chunk_audio(str(corrupted_file))
    assert isinstance(res_chunk_corrupt, errors.ToolErrorPayload)
    assert res_chunk_corrupt.code == errors.UNREADABLE_MEDIA

    # get_voice_library: Never fails
    voices = get_voice_library()
    assert len(voices.voices) >= len(config.INDIC_LANGS)
