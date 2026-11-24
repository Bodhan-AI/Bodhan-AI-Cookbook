from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import UTCDateTime, utcnow


class SavedStory(Base):
    """A user's bookmark of someone's (or their own) story."""

    __tablename__ = "saved_stories"
    __table_args__ = (UniqueConstraint("user_id", "story_id", name="user_story"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("heritage_stories.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
