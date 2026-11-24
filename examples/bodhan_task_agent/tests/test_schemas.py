from __future__ import annotations

import pytest
from pydantic import ValidationError

from bodhan_mcp.schemas.ocr import OcrInput
from bodhan_mcp.schemas.synthesize import SynthesizeInput
from bodhan_mcp.schemas.transcribe import TranscribeInput
from bodhan_mcp.schemas.translate import TranslateInput

FORM_STYLE = [
    (TranslateInput, {"text": "Hello", "text_path": "", "target_language": "hi", "output_path": "", "script": ""}),
    (TranslateInput, {"text": "  ", "text_path": "notes.md", "target_language": "ta"}),
    (TranscribeInput, {"audio_path": "a.wav", "language": "", "output_path": " "}),
    (
        SynthesizeInput,
        {"text": "नमस्ते", "text_path": "", "language": "hi", "voice": "", "gender": "", "style": "",
         "format": "", "output_path": ""},
    ),
    (OcrInput, {"file_path": "a.pdf", "pages": "", "table_format": "", "output_path": ""}),
]  # fmt: skip


@pytest.mark.parametrize(("model", "arguments"), FORM_STYLE)
def test_blank_optional_fields_count_as_not_given(model: type, arguments: dict) -> None:
    parsed = model.model_validate(arguments)
    for key, value in arguments.items():
        if isinstance(value, str) and not value.strip():
            assert getattr(parsed, key) == model.model_fields[key].default


def test_defaults_apply_for_blank_choices() -> None:
    parsed = SynthesizeInput.model_validate({"text": "x", "language": "hi", "format": "", "output_path": ""})
    assert parsed.format == "wav" and parsed.output_path is None
    assert OcrInput.model_validate({"file_path": "a.png", "table_format": ""}).table_format == "markdown"


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (TranscribeInput, {"audio_path": ""}),
        (OcrInput, {"file_path": "   "}),
        (SynthesizeInput, {"text": "x", "language": ""}),
        (TranslateInput, {"text": "x", "target_language": ""}),
        (TranslateInput, {"text": "", "text_path": "", "target_language": "hi"}),
    ],
)
def test_blank_required_fields_are_still_rejected(model: type, arguments: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(arguments)


def test_non_blank_values_are_still_validated() -> None:
    with pytest.raises(ValidationError):
        SynthesizeInput.model_validate({"text": "x", "language": "hi", "voice": "kavya"})
    with pytest.raises(ValidationError):
        OcrInput.model_validate({"file_path": "a.pdf", "pages": "3-1"})
    with pytest.raises(ValidationError):
        TranscribeInput.model_validate({"audio_path": "a.wav", "surprise": ""})
