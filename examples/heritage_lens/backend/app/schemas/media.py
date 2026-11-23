from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AudioKind, DocumentType, OcrStatus


class AudioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    story_id: int | None
    kind: AudioKind
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    language: str | None
    duration: float | None
    transcript: str | None
    transcript_edited: str | None
    transcript_language: str | None
    transcript_segments: list[Any] | None
    transcript_provider: str | None
    transcribed_at: datetime | None
    narrated_text: str | None
    narration_voice: str | None
    narration_style: str | None
    translation_id: int | None
    has_original_audio: bool = False
    has_generated_audio: bool = False
    created_at: datetime
    provenance: str = "user_authored"
    transcript_provenance: str | None = None

    @classmethod
    def from_model(cls, rec) -> "AudioOut":
        out = cls.model_validate(rec)
        out.has_original_audio = bool(rec.original_audio_path)
        out.has_generated_audio = bool(rec.generated_audio_path)
        out.provenance = "live_bodhan" if rec.kind.value == "generated" else "user_authored"
        out.transcript_provenance = "live_bodhan" if rec.transcript else None
        return out


class TranscriptEdit(BaseModel):
    transcript_edited: str = Field(max_length=200_000)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    story_id: int | None
    original_filename: str
    mime_type: str
    size_bytes: int
    page_count: int | None
    document_type: DocumentType
    description: str | None
    extracted_text: str | None
    extracted_edited_text: str | None
    ocr_pages: list[Any] | None
    ocr_status: OcrStatus
    ocr_error: str | None
    ocr_provider: str | None
    ocr_at: datetime | None
    created_at: datetime
    updated_at: datetime
    ocr_provenance: str | None = None


def document_out(doc) -> "DocumentOut":
    out = DocumentOut.model_validate(doc)
    out.ocr_provenance = "live_bodhan" if doc.extracted_text is not None else None
    return out


class DocumentTextEdit(BaseModel):
    extracted_edited_text: str = Field(max_length=500_000)
