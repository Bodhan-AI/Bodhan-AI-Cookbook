from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import UTCDateTime, enum_column, utcnow
from app.models.enums import CapsuleStatus, Privacy


class TimeCapsule(Base):
    """A saved message with an intended release date. No scheduler or notifier exists, so this
    records intent only; nothing is delivered automatically."""

    __tablename__ = "time_capsules"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    dedication: Mapped[str | None] = mapped_column(String(200))
    audio_path: Mapped[str | None] = mapped_column(String(500))
    intended_release_date: Mapped[date | None] = mapped_column(Date, index=True)
    privacy: Mapped[Privacy] = mapped_column(enum_column(Privacy), default=Privacy.private)
    status: Mapped[CapsuleStatus] = mapped_column(enum_column(CapsuleStatus), default=CapsuleStatus.draft)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="time_capsules")
