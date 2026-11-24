"""Read-only public discovery: facets, story bundle, map locations, timeline."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_optional_user
from app.core.errors import NotFoundError
from app.models import (
    AudioRecording, HeritageImage, HeritageStory, HistoricalDocument, Privacy, PublicationStatus, Translation,
    Transliteration, User,
)
from app.schemas.ai import TranslationOut, TransliterationOut
from app.schemas.media import AudioOut, DocumentOut, document_out
from app.schemas.story import StoryOut
from app.api.routes.images import ImageOut
from app.api.routes.stories import _can_view, to_out

router = APIRouter(tags=["discover"])
PUBLIC = (HeritageStory.privacy == Privacy.public, HeritageStory.publication_status == PublicationStatus.published)


@router.get("/discover/facets")
def facets(db: Session = Depends(get_db)):
    """Distinct values that actually exist in the public archive, with counts (for filter menus)."""
    def count_by(col):
        rows = db.execute(select(col, func.count()).where(*PUBLIC, col.is_not(None)).group_by(col)
                          .order_by(func.count().desc())).all()
        return [{"value": v.value if hasattr(v, "value") else v, "count": n} for v, n in rows if v not in ("", None)]
    return {"countries": count_by(HeritageStory.country), "regions": count_by(HeritageStory.region),
            "languages": count_by(HeritageStory.language), "categories": count_by(HeritageStory.category),
            "periods": count_by(HeritageStory.historical_period),
            "total": db.scalar(select(func.count()).select_from(HeritageStory).where(*PUBLIC)) or 0}


@router.get("/stories/{story_id}/bundle")
def story_bundle(story_id: int, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db)):
    """Everything the story page needs. Owners see all of it; others only public, published content,
    and never the owner's private scans."""
    story = db.get(HeritageStory, story_id)
    if story is None or not _can_view(story, user):
        raise NotFoundError("Story not found.")
    owner = user is not None and user.id == story.user_id
    translations = db.scalars(select(Translation).where(Translation.story_id == story_id)
                              .order_by(Translation.id)).all()
    translit = db.scalars(select(Transliteration).where(Transliteration.story_id == story_id)
                          .order_by(Transliteration.id)).all()
    audio = db.scalars(select(AudioRecording).where(AudioRecording.story_id == story_id)
                       .order_by(AudioRecording.id)).all()
    images = db.scalars(select(HeritageImage).where(HeritageImage.story_id == story_id).order_by(HeritageImage.id)).all()
    docs = db.scalars(select(HistoricalDocument).where(HistoricalDocument.story_id == story_id)).all() if owner else []
    contributor = story.attribution or story.user.display_name or story.user.username
    return {
        "story": to_out(db, [story])[0], "contributor": contributor, "is_owner": owner,
        "translations": [TranslationOut.model_validate(t) for t in translations],
        "transliterations": [TransliterationOut.model_validate(t) for t in translit],
        "audio": [AudioOut.from_model(a) for a in audio],
        "images": [ImageOut.model_validate(i) for i in images],
        "documents": [document_out(d) for d in docs],
    }


@router.get("/locations")
def locations(category: str | None = None, db: Session = Depends(get_db)):
    """Public stories that have contributor-supplied coordinates. Nothing is geocoded or guessed."""
    stmt = select(HeritageStory).where(*PUBLIC, HeritageStory.latitude.is_not(None),
                                       HeritageStory.longitude.is_not(None))
    if category:
        stmt = stmt.where(HeritageStory.category == category)
    rows = db.scalars(stmt.order_by(HeritageStory.id)).all()
    cover = {i.story_id: i.id for i in db.scalars(select(HeritageImage).where(
        HeritageImage.story_id.in_([r.id for r in rows] or [0])).order_by(HeritageImage.id.desc()))}
    return {"items": [{"id": s.id, "title": s.title, "description": s.description, "country": s.country,
                       "region": s.region, "city": s.city, "village": s.village, "language": s.language,
                       "category": s.category.value if s.category else None, "latitude": s.latitude,
                       "longitude": s.longitude, "cover_image_id": cover.get(s.id), "provenance": "user_authored"}
                      for s in rows],
            "total_public": db.scalar(select(func.count()).select_from(HeritageStory).where(*PUBLIC)) or 0}


@router.get("/timeline")
def timeline(country: str | None = None, language: str | None = None, limit: int = Query(default=200, le=500),
             db: Session = Depends(get_db)):
    """Dated stories in order, plus undated ones listed separately (dates are never invented)."""
    base = select(HeritageStory).where(*PUBLIC)
    if country:
        base = base.where(func.lower(HeritageStory.country) == country.lower())
    if language:
        base = base.where(HeritageStory.language == language)
    dated = db.scalars(base.where(HeritageStory.event_year.is_not(None))
                       .order_by(HeritageStory.event_year, HeritageStory.id).limit(limit)).all()
    undated = db.scalars(base.where(HeritageStory.event_year.is_(None))
                         .order_by(HeritageStory.id.desc()).limit(50)).all()
    return {"dated": to_out(db, dated), "undated": to_out(db, undated)}
