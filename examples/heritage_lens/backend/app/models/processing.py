from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import UTCDateTime, utcnow


class Translation(Base):
    """original_text is a snapshot of what was sent; the story itself is never modified."""

    __tablename__ = "translations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("heritage_stories.id", ondelete="CASCADE"), index=True)
    # Informational only: indic-translate has no source-language field and infers the source itself.
    source_language: Mapped[str | None] = mapped_column(String(12))
    target_language: Mapped[str] = mapped_column(String(12), index=True)
    script: Mapped[str] = mapped_column(String(10), default="native")
    original_text: Mapped[str] = mapped_column(Text)
    translated_text: Mapped[str] = mapped_column(Text)   # raw machine output
    edited_text: Mapped[str | None] = mapped_column(Text)  # contributor's corrections
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    segments: Mapped[list | None] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(50), default="bodhan:indic-translate")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    story = relationship("HeritageStory", back_populates="translations")


class Transliteration(Base):
    """Same language, different writing system. There is deliberately no 'source script' column:
    the API takes a single language and an output script (roman|native)."""

    __tablename__ = "transliterations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("heritage_stories.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(12), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    target_script: Mapped[str] = mapped_column(String(10))
    converted_text: Mapped[str] = mapped_column(Text)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    segments: Mapped[list | None] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(50), default="bodhan:indic-transliterate")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    story = relationship("HeritageStory", back_populates="transliterations")
