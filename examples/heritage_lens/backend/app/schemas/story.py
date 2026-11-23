from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import Privacy, PublicationStatus, StoryCategory

LANG_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$"


class _StoryFields(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    country: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    village: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, pattern=LANG_PATTERN)
    category: StoryCategory | None = None
    historical_period: str | None = Field(default=None, max_length=100)
    event_year: int | None = Field(default=None, ge=-3000, le=2100)
    event_year_approximate: bool = True
    original_text: str | None = Field(default=None, max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    privacy: Privacy = Privacy.private
    publication_status: PublicationStatus = PublicationStatus.draft
    consent_confirmed: bool = False
    attribution: str | None = Field(default=None, max_length=200)

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        cleaned = [t.strip() for t in v if t and t.strip()]
        if any(len(t) > 40 for t in cleaned):
            raise ValueError("Each tag must be at most 40 characters.")
        return cleaned

    @model_validator(mode="after")
    def _coords_together(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither.")
        return self


class StoryCreate(_StoryFields):
    pass


class StoryUpdate(BaseModel):
    """Partial update: only fields present in the request are changed."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    country: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    village: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, pattern=LANG_PATTERN)
    category: StoryCategory | None = None
    historical_period: str | None = Field(default=None, max_length=100)
    event_year: int | None = Field(default=None, ge=-3000, le=2100)
    event_year_approximate: bool | None = None
    original_text: str | None = Field(default=None, max_length=200_000)
    tags: list[str] | None = Field(default=None, max_length=20)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    privacy: Privacy | None = None
    publication_status: PublicationStatus | None = None
    consent_confirmed: bool | None = None
    attribution: str | None = Field(default=None, max_length=200)


class StoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    title: str
    description: str | None
    country: str | None
    region: str | None
    city: str | None
    village: str | None
    language: str | None
    category: StoryCategory | None
    historical_period: str | None
    event_year: int | None
    event_year_approximate: bool
    original_text: str | None
    tags: list[str]
    latitude: float | None
    longitude: float | None
    privacy: Privacy
    publication_status: PublicationStatus
    consent_confirmed: bool
    consent_at: datetime | None
    attribution: str | None
    created_at: datetime
    updated_at: datetime
    provenance: str = "user_authored"
    cover_image_id: int | None = None
