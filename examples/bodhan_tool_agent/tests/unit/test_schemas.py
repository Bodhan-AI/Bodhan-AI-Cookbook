"""Unit tests for MCP schemas and Pydantic validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from bodhan_mcp.schemas import (
    ExtractTextFromDocumentInput,
    SynthesizeSpeechInput,
    TranscribeAudioInput,
    TranslateTextInput,
)


def test_transcribe_audio_input_valid():
    inp = TranscribeAudioInput(file_path="audio/test.wav", language="hi")
    assert inp.file_path == "audio/test.wav"
    assert inp.language == "hi"


def test_translate_text_input_valid():
    inp = TranslateTextInput(text="Hello world", target_language="ta", script="native")
    assert inp.text == "Hello world"
    assert inp.target_language == "ta"
    assert inp.script == "native"


def test_translate_text_input_invalid_script():
    with pytest.raises(ValidationError):
        TranslateTextInput(text="Hello", target_language="ta", script="invalid_script")  # type: ignore


def test_synthesize_speech_input_valid():
    inp = SynthesizeSpeechInput(text="शुभ प्रभात", language="hi", voice="Kavya")
    assert inp.voice == "Kavya"
    assert inp.gender_preference == "female"


def test_extract_text_document_input_valid():
    inp = ExtractTextFromDocumentInput(file_path="doc.pdf", table_format="markdown")
    assert inp.table_format == "markdown"
