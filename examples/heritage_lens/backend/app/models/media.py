from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import UTCDateTime, enum_column, utcnow
from app.models.enums import AudioKind, DocumentType, OcrStatus


class AudioRecording(Base):
    """kind=original: a human recording (immutable). kind=generated: indic-speak narration.

    transcript          -> raw machine output from indic-transcribe (replaced only by a new machine run)
    transcript_edited   -> the contributor's corrections; the raw transcript is kept alongside
    """

    __tablename__ = "audio_recordings"
    __table_args__ = (
        CheckConstraint("original_audio_path IS NOT NULL OR generated_audio_path IS NOT NULL", name="has_audio_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("heritage_stories.id", ondelete="SET NULL"), index=True)
    kind: Mapped[AudioKind] = mapped_column(enum_column(AudioKind), default=AudioKind.original, index=True)
    original_audio_path: Mapped[str | None] = mapped_column(String(500))
    generated_audio_path: Mapped[str | None] = mapped_column(String(500))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(12), index=True)
    duration: Mapped[float | None] = mapped_column(Float)  # seconds

    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_edited: Mapped[str | None] = mapped_column(Text)
    transcript_language: Mapped[str | None] = mapped_column(String(12))
    transcript_segments: Mapped[list | None] = mapped_column(JSON)
    transcript_provider: Mapped[str | None] = mapped_column(String(50))
    transcribed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    narrated_text: Mapped[str | None] = mapped_column(Text)
    narration_voice: Mapped[str | None] = mapped_column(String(50))
    narration_style: Mapped[str | None] = mapped_column(String(60))
    translation_id: Mapped[int | None] = mapped_column(ForeignKey("translations.id", ondelete="SET NULL"))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    story = relationship("HeritageStory", back_populates="audio_recordings")


class HistoricalDocument(Base):
    """stored_path is the untouched original. OCR output lives in separate columns."""

    __tablename__ = "historical_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("heritage_stories.id", ondelete="SET NULL"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    document_type: Mapped[DocumentType] = mapped_column(enum_column(DocumentType), default=DocumentType.other)
    description: Mapped[str | None] = mapped_column(Text)

    extracted_text: Mapped[str | None] = mapped_column(Text)          # raw OCR markdown, all pages in order
    extracted_edited_text: Mapped[str | None] = mapped_column(Text)   # contributor's corrections
    ocr_pages: Mapped[list | None] = mapped_column(JSON)              # per-page markdown, blocks, errors
    ocr_status: Mapped[OcrStatus] = mapped_column(enum_column(OcrStatus), default=OcrStatus.not_run)
    ocr_error: Mapped[str | None] = mapped_column(String(500))
    ocr_provider: Mapped[str | None] = mapped_column(String(50))
    ocr_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    story = relationship("HeritageStory", back_populates="documents")


class HeritageImage(Base):
    __tablename__ = "heritage_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("heritage_stories.id", ondelete="SET NULL"), index=True)
    image_path: Mapped[str] = mapped_column(String(500))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    caption: Mapped[str | None] = mapped_column(String(500))
    attribution: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    story = relationship("HeritageStory", back_populates="images")
