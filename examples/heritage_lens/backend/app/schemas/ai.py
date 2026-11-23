from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROVENANCE = "live_bodhan"  # every field in these responses came from a real Bodhan call


class TranscribeRequest(BaseModel):
    audio_id: int
    language: str = Field(pattern=r"^[A-Za-z]{2,3}$")
    force: bool = False


class TranslateRequest(BaseModel):
    story_id: int | None = None
    text: str | None = Field(default=None, max_length=50_000)
    target_language: str = Field(max_length=12)
    script: str = "native"
    save: bool = False

    @model_validator(mode="after")
    def _source(self):
        if not (self.text and self.text.strip()) and self.story_id is None:
            raise ValueError("Provide 'text' or a 'story_id' whose original text should be translated.")
        if self.save and self.story_id is None:
            raise ValueError("'save' requires a story_id.")
        return self


class TransliterateRequest(BaseModel):
    story_id: int | None = None
    text: str = Field(min_length=1, max_length=20_000)
    language: str = Field(max_length=12)
    script: str = "roman"
    save: bool = False


class OcrRequest(BaseModel):
    document_id: int
    table_format: str | None = Field(default=None, pattern="^(html|markdown)$")
    max_tokens: int | None = Field(default=None, ge=256, le=16384)
    force: bool = False


class SpeakRequest(BaseModel):
    story_id: int | None = None
    translation_id: int | None = None
    text: str | None = Field(default=None, max_length=5_000)
    language: str = Field(max_length=12)
    voice: str = Field(max_length=40)
    style: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _source(self):
        if not (self.text and self.text.strip()) and self.story_id is None and self.translation_id is None:
            raise ValueError("Provide 'text', a 'story_id' or a 'translation_id'.")
        return self


class SegmentOut(BaseModel):
    index: int
    source_text: str
    result_text: str
    truncated: bool = False


class TranslationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    story_id: int
    source_language: str | None
    target_language: str
    script: str
    original_text: str
    translated_text: str
    edited_text: str | None
    truncated: bool
    segments: list[Any] | None
    provider: str
    created_at: datetime
    updated_at: datetime
    provenance: str = PROVENANCE


class TranslationEdit(BaseModel):
    edited_text: str = Field(max_length=200_000)


class TransliterationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    story_id: int | None
    language: str
    source_text: str
    target_script: str
    converted_text: str
    truncated: bool
    segments: list[Any] | None
    provider: str
    created_at: datetime
    provenance: str = PROVENANCE


class TranslateResponse(BaseModel):
    provenance: str = PROVENANCE
    translation: str
    target_language: str
    script: str
    truncated: bool
    segments: list[SegmentOut]
    saved: TranslationOut | None = None


class TransliterateResponse(BaseModel):
    provenance: str = PROVENANCE
    converted_text: str
    language: str
    script: str
    truncated: bool
    segments: list[SegmentOut]
    saved: TransliterationOut | None = None
