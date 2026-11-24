from dataclasses import fields, is_dataclass

from bodhan_mcp.client import OcrResult, SpeechResult, TranscribeResult, TranslateResult


def test_result_types_are_plain_dataclasses():
    for result_type in (TranscribeResult, TranslateResult, SpeechResult, OcrResult):
        assert is_dataclass(result_type)


def test_transcribe_result_shape():
    assert [field.name for field in fields(TranscribeResult)] == ["text"]


def test_translate_result_shape():
    assert [field.name for field in fields(TranslateResult)] == ["text", "truncated"]


def test_speech_result_shape():
    assert [field.name for field in fields(SpeechResult)] == ["audio_bytes", "format"]


def test_ocr_result_shape():
    assert [field.name for field in fields(OcrResult)] == ["markdown", "blocks"]
