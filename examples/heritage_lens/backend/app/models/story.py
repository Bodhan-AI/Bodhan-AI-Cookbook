from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import UTCDateTime, enum_column, utcnow
from app.models.enums import Privacy, PublicationStatus, StoryCategory


class HeritageStory(Base):
    """The contributor's own account. AI output never writes to this table's text fields."""

    __tablename__ = "heritage_stories"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="latitude_range"),
        CheckConstraint("longitude IS NULL OR (longitude >= -180 AND longitude <= 180)", name="longitude_range"),
        CheckConstraint("event_year IS NULL OR (event_year >= -3000 AND event_year <= 2100)", name="event_year_range"),
        Index("ix_stories_public_listing", "privacy", "publication_status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(100), index=True)
    region: Mapped[str | None] = mapped_column(String(100), index=True)
    city: Mapped[str | None] = mapped_column(String(100))
    village: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(12), index=True)  # BCP-47-ish code, e.g. "te"
    category: Mapped[StoryCategory | None] = mapped_column(enum_column(StoryCategory), index=True)
    historical_period: Mapped[str | None] = mapped_column(String(100), index=True)
    # Only set when the contributor supplies it; timeline never guesses dates.
    event_year: Mapped[int | None] = mapped_column(Integer, index=True)
    event_year_approximate: Mapped[bool] = mapped_column(Boolean, default=True)
    original_text: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Only set when the contributor (or a verified geocoder) supplied them. Never guessed.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    privacy: Mapped[Privacy] = mapped_column(enum_column(Privacy), default=Privacy.private)
    publication_status: Mapped[PublicationStatus] = mapped_column(enum_column(PublicationStatus),
                                                                  default=PublicationStatus.draft)
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attribution: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="stories")
    # Derived records die with the story...
    translations = relationship("Translation", back_populates="story", cascade="all, delete-orphan",
                                passive_deletes=True)
    transliterations = relationship("Transliteration", back_populates="story", cascade="all, delete-orphan",
                                    passive_deletes=True)
    # ...but originals (recordings, scans, photographs) are detached, not destroyed, unless the
    # owner explicitly asks for them to be deleted too.
    audio_recordings = relationship("AudioRecording", back_populates="story", passive_deletes=True)
    documents = relationship("HistoricalDocument", back_populates="story", passive_deletes=True)
    images = relationship("HeritageImage", back_populates="story", passive_deletes=True)
