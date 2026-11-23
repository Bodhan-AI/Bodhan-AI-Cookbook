from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_file_store, get_optional_user
from app.core.errors import AuthenticationError, NotFoundError, ValidationFailed
from app.models import (
    AudioRecording, HeritageImage, HeritageStory, HistoricalDocument, Privacy, PublicationStatus,
    StoryCategory, User,
)
from app.schemas.common import Page
from app.schemas.story import StoryCreate, StoryOut, StoryUpdate

router = APIRouter(prefix="/stories", tags=["stories"])


def _enforce_publication_rules(story: HeritageStory) -> None:
    """Only publish with the contributor's explicit consent (never inferred)."""
    if story.publication_status == PublicationStatus.published and not story.consent_confirmed:
        raise ValidationFailed("The contributor must explicitly confirm consent before a story can be published.",
                               code="consent_required")


def to_out(db: Session, stories) -> list[StoryOut]:
    """Serialise stories, attaching the id of each story's first photograph (the cover) in one query."""
    stories = list(stories)
    ids = [s.id for s in stories]
    covers = dict(db.execute(select(HeritageImage.story_id, func.min(HeritageImage.id))
                             .where(HeritageImage.story_id.in_(ids or [0])).group_by(HeritageImage.story_id)).all())
    outs = [StoryOut.model_validate(s) for s in stories]
    for o in outs:
        o.cover_image_id = covers.get(o.id)
    return outs


def _can_view(story: HeritageStory, user: User | None) -> bool:
    if user is not None and story.user_id == user.id:
        return True
    return story.privacy == Privacy.public and story.publication_status == PublicationStatus.published


@router.post("", response_model=StoryOut, status_code=201)
def create_story(body: StoryCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = HeritageStory(user_id=user.id, **body.model_dump())
    if story.consent_confirmed:
        story.consent_at = datetime.now(timezone.utc)
    if not story.attribution:
        story.attribution = user.display_name or user.username
    _enforce_publication_rules(story)
    db.add(story)
    db.commit()
    return story


@router.get("", response_model=Page[StoryOut])
def list_stories(
    scope: Literal["mine", "public"] = "mine",
    q: str | None = Query(default=None, max_length=100),
    country: str | None = None,
    region: str | None = None,
    language: str | None = None,
    category: StoryCategory | None = None,
    period: str | None = None,
    status: PublicationStatus | None = None,
    sort: Literal["newest", "oldest", "title", "year"] = "newest",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    stmt = select(HeritageStory)
    if scope == "mine":
        if user is None:
            raise AuthenticationError("Please sign in to see your stories.")
        stmt = stmt.where(HeritageStory.user_id == user.id)
    else:  # public archive: published AND public only; private stories are never listed
        stmt = stmt.where(HeritageStory.privacy == Privacy.public,
                          HeritageStory.publication_status == PublicationStatus.published)
    if q:
        stmt = stmt.where(or_(HeritageStory.title.contains(q, autoescape=True),
                              HeritageStory.description.contains(q, autoescape=True)))
    if country:
        stmt = stmt.where(func.lower(HeritageStory.country) == country.lower())
    if region:
        stmt = stmt.where(func.lower(HeritageStory.region) == region.lower())
    if language:
        stmt = stmt.where(HeritageStory.language == language)
    if category:
        stmt = stmt.where(HeritageStory.category == category)
    if period:
        stmt = stmt.where(func.lower(HeritageStory.historical_period) == period.lower())
    if status and scope == "mine":
        stmt = stmt.where(HeritageStory.publication_status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order = {"newest": [HeritageStory.created_at.desc(), HeritageStory.id.desc()],
             "oldest": [HeritageStory.created_at.asc(), HeritageStory.id.asc()],
             "title": [func.lower(HeritageStory.title).asc(), HeritageStory.id.asc()],
             "year": [HeritageStory.event_year.is_(None).asc(), HeritageStory.event_year.asc(),
                      HeritageStory.id.asc()]}[sort]
    items = db.scalars(stmt.order_by(*order)
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[StoryOut](items=to_out(db, items), total=total, page=page,
                          page_size=page_size)


@router.get("/{story_id}", response_model=StoryOut)
def get_story(story_id: int, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    story = db.get(HeritageStory, story_id)
    if story is None or not _can_view(story, user):
        raise NotFoundError("Story not found.")
    return to_out(db, [story])[0]


@router.put("/{story_id}", response_model=StoryOut)
def update_story(story_id: int, body: StoryUpdate, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    story = db.get(HeritageStory, story_id)
    if story is None or story.user_id != user.id:
        raise NotFoundError("Story not found.")
    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is None:
        raise ValidationFailed("Title cannot be empty.")
    for field, value in data.items():
        setattr(story, field, value)
    if data.get("consent_confirmed") and story.consent_at is None:
        story.consent_at = datetime.now(timezone.utc)
    if (story.latitude is None) != (story.longitude is None):
        raise ValidationFailed("Provide both latitude and longitude, or neither.")
    _enforce_publication_rules(story)
    db.commit()
    return story


@router.delete("/{story_id}", status_code=204)
def delete_story(story_id: int, delete_media: bool = False, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db), store=Depends(get_file_store)):
    """Deletes the story and its translations/transliterations. Original recordings, scans and
    photographs are detached and kept unless delete_media=true is passed explicitly."""
    story = db.get(HeritageStory, story_id)
    if story is None or story.user_id != user.id:
        raise NotFoundError("Story not found.")
    paths: list[str | None] = []
    if delete_media:
        for rec in list(story.audio_recordings):
            paths += [rec.original_audio_path, rec.generated_audio_path]
            db.delete(rec)
        for doc in list(story.documents):
            paths.append(doc.stored_path)
            db.delete(doc)
        for img in list(story.images):
            paths.append(img.image_path)
            db.delete(img)
    else:  # generated narration belongs to the story's derived content; originals stay
        for rec in list(story.audio_recordings):
            if rec.original_audio_path is None:
                paths.append(rec.generated_audio_path)
                db.delete(rec)
    db.delete(story)
    db.commit()
    for p in paths:
        store.delete(p)
    return Response(status_code=204)
