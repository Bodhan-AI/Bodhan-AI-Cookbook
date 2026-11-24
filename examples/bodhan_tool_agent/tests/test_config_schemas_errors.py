"""Tests for config, schemas, and error definitions (Slice 2)."""

import pytest
from pydantic import ValidationError

from bodhan_mcp import config, errors, schemas


def test_config_prices_and_limits():
    """Verify prices and operational limits adhere to SPEC §6.1."""
    assert config.PRICES["transcribe_per_audio_min"] == 0.10
    assert config.PRICES["translate_per_10k_output_tokens"] == 0.20
    assert config.PRICES["speak_per_10k_chars"] == 6.00
    assert config.PRICES["ocr_per_image"] == 0.20

    assert config.AUDIO_CHUNK_TARGET_S == 25.0
    assert config.AUDIO_CHUNK_HARD_S == 30.0
    assert config.SILENCE_MIN_MS == 250
    assert config.TRANSLATE_CONTEXT_TOK == 32_000
    assert config.SPEAK_MAX_CHARS == 5_000


def test_config_supported_languages_rule_f6():
    """Verify supported languages per model adhere strictly to Standing Rule F6."""
    # transcribe, translate, and ocr must include English
    assert "en" in config.SUPPORTED_LANGS["transcribe"]
    assert "en" in config.SUPPORTED_LANGS["translate"]
    assert "en" in config.SUPPORTED_LANGS["ocr"]

    # speak must strictly exclude English
    assert "en" not in config.SUPPORTED_LANGS["speak"]

    # Minimum required Indic languages must be present
    for lang in ["hi", "ta", "te", "bn", "mr", "kn", "ml", "gu", "pa", "or", "as", "ur"]:
        assert lang in config.SUPPORTED_LANGS["transcribe"]
        assert lang in config.SUPPORTED_LANGS["translate"]
        assert lang in config.SUPPORTED_LANGS["ocr"]
        assert lang in config.SUPPORTED_LANGS["speak"]
        assert lang in config.VOICE_LIBRARY
        assert len(config.VOICE_LIBRARY[lang]) > 0


def test_schemas_transcribe():
    """Verify transcription input and output schemas."""
    in_obj = schemas.TranscribeIn(input_path="/path/to/audio.wav", language_hint="hi")
    assert in_obj.input_path == "/path/to/audio.wav"
    assert in_obj.language_hint == "hi"

    out_obj = schemas.TranscribeOut(
        transcript="नमस्ते दुनिया",
        language="hi",
        language_confidence=0.95,
        segments=[
            schemas.Segment(start_s=0.0, end_s=1.5, text="नमस्ते"),
            schemas.Segment(start_s=1.5, end_s=3.0, text="दुनिया"),
        ],
        duration_s=3.0,
        cost_inr=0.005,
    )
    assert len(out_obj.segments) == 2
    assert out_obj.segments[0].start_s == 0.0


def test_schemas_translate():
    """Verify translation input and output schemas."""
    in_obj = schemas.TranslateIn(text="Hello", source_lang="en", target_lang="ta")
    assert in_obj.text == "Hello"

    out_obj = schemas.TranslateOut(translated="வணக்கம்", cost_inr=0.001)
    assert out_obj.translated == "வணக்கம்"
    assert out_obj.cost_inr == 0.001


def test_schemas_speak():
    """Verify speech synthesis input and output schemas."""
    in_obj = schemas.SpeakIn(text="வணக்கம்", language="ta", voice_id="ta_female_1")
    assert in_obj.voice_id == "ta_female_1"

    out_obj = schemas.SpeakOut(
        path="/path/to/speech.wav",
        duration_s=2.4,
        chars=7,
        ai_generated=True,
        cost_inr=0.0042,
    )
    assert out_obj.ai_generated is True


def test_schemas_ocr():
    """Verify OCR page and document schemas."""
    page_in = schemas.OcrPageIn(image_path="/path/to/page.png")
    assert page_in.image_path == "/path/to/page.png"
    page_out = schemas.OcrPageOut(markdown="# Title", page=1, cost_inr=0.20)
    assert page_out.page == 1

    doc_in = schemas.OcrDocIn(pdf_path="/path/to/doc.pdf")
    assert doc_in.pdf_path == "/path/to/doc.pdf"
    doc_out = schemas.OcrDocOut(markdown="# Title\n\nPage 2 content", pages=2, cost_inr=0.40)
    assert doc_out.pages == 2


def test_schemas_inspect_and_helpers_rule_f5():
    """Verify inspect_media and helper schemas per Standing Rule F5."""
    inspect_out = schemas.InspectOut(
        kind="audio",
        duration_s=12.5,
        pages=None,
        format="wav",
        size_mb=1.2,
    )
    assert inspect_out.kind == "audio"
    assert inspect_out.duration_s == 12.5

    chunk_in = schemas.ChunkAudioIn(path="/path/to/audio.wav")
    assert chunk_in.path == "/path/to/audio.wav"
    chunk_out = schemas.ChunkAudioOut(
        chunks=[
            schemas.AudioChunk(path="/path/to/chunk_0.wav", start_s=0.0, end_s=25.0),
            schemas.AudioChunk(path="/path/to/chunk_1.wav", start_s=24.5, end_s=40.0),
        ]
    )
    assert len(chunk_out.chunks) == 2
    assert chunk_out.chunks[1].start_s == 24.5

    voice_out = schemas.VoiceLibraryOut(voices={"hi": ["hi_female_1", "hi_male_1"]})
    assert "hi" in voice_out.voices


def test_schemas_extra_fields_forbidden():
    """Verify extra attributes are rejected by strict schemas."""
    with pytest.raises(ValidationError):
        schemas.TranslateIn(
            text="Hello",
            source_lang="en",
            target_lang="hi",
            unexpected_field="disallowed",  # type: ignore
        )


def test_errors_and_payload():
    """Verify ToolErrorPayload and helper builder functions."""
    err = errors.make_tool_error(
        code=errors.FILE_NOT_FOUND,
        message="Audio file not found",
        hint="Check input_path",
        details={"path": "/missing.wav"},
    )
    assert err.code == errors.FILE_NOT_FOUND
    assert err.details["path"] == "/missing.wav"

    # Test error wrapping in exception
    exc = errors.ToolExecutionError(err)
    assert exc.payload.code == errors.FILE_NOT_FOUND
    assert str(exc) == "Audio file not found"
