from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.errors import NotFoundError
from app.models import HeritageStory, SavedStory, User
from app.schemas.common import Page
from app.schemas.story import StoryOut
from app.api.routes.stories import _can_view, to_out

router = APIRouter(prefix="/saved", tags=["saved"])


@router.get("", response_model=Page[StoryOut])
def list_saved(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(HeritageStory).join(SavedStory, SavedStory.story_id == HeritageStory.id)
                      .where(SavedStory.user_id == user.id).order_by(SavedStory.created_at.desc())).all()
    visible = [s for s in rows if _can_view(s, user)]  # a story made private later disappears from here
    start = (page - 1) * page_size
    return Page[StoryOut](items=to_out(db, visible[start:start + page_size]),
                          total=len(visible), page=page, page_size=page_size)


@router.get("/ids")
def saved_ids(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"story_ids": list(db.scalars(select(SavedStory.story_id).where(SavedStory.user_id == user.id)))}


@router.put("/{story_id}", status_code=204)
def save_story(story_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = db.get(HeritageStory, story_id)
    if story is None or not _can_view(story, user):
        raise NotFoundError("Story not found.")
    db.add(SavedStory(user_id=user.id, story_id=story_id))
    try:
        db.commit()
    except IntegrityError:  # already saved: idempotent
        db.rollback()
    return Response(status_code=204)


@router.delete("/{story_id}", status_code=204)
def unsave_story(story_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.scalar(select(SavedStory).where(SavedStory.user_id == user.id, SavedStory.story_id == story_id))
    if row:
        db.delete(row)
        db.commit()
    return Response(status_code=204)
