"""Read / correct / delete saved translations and transliterations (ownership enforced)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned
from app.models import Translation, Transliteration, User
from app.schemas.ai import TranslationEdit, TranslationOut, TransliterationOut
from app.schemas.common import Page

router = APIRouter(tags=["derived"])


@router.get("/translations", response_model=Page[TranslationOut])
def list_translations(story_id: int | None = None, page: int = Query(default=1, ge=1),
                      page_size: int = Query(default=20, ge=1, le=100),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Translation).where(Translation.user_id == user.id)
    if story_id is not None:
        stmt = stmt.where(Translation.story_id == story_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Translation.created_at.desc(), Translation.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[TranslationOut](items=[TranslationOut.model_validate(r) for r in rows], total=total, page=page,
                                page_size=page_size)


@router.get("/translations/{translation_id}", response_model=TranslationOut)
def get_translation(translation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_owned(db, Translation, translation_id, user)


@router.put("/translations/{translation_id}", response_model=TranslationOut)
def edit_translation(translation_id: int, body: TranslationEdit, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Store the contributor's correction in edited_text; the machine output and the story stay unchanged."""
    tr = get_owned(db, Translation, translation_id, user)
    tr.edited_text = body.edited_text
    db.commit()
    return tr


@router.delete("/translations/{translation_id}", status_code=204)
def delete_translation(translation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(get_owned(db, Translation, translation_id, user))
    db.commit()
    return Response(status_code=204)


@router.get("/transliterations", response_model=Page[TransliterationOut])
def list_transliterations(story_id: int | None = None, page: int = Query(default=1, ge=1),
                          page_size: int = Query(default=20, ge=1, le=100),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(Transliteration).where(Transliteration.user_id == user.id)
    if story_id is not None:
        stmt = stmt.where(Transliteration.story_id == story_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Transliteration.created_at.desc(), Transliteration.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[TransliterationOut](items=[TransliterationOut.model_validate(r) for r in rows], total=total,
                                    page=page, page_size=page_size)


@router.delete("/transliterations/{item_id}", status_code=204)
def delete_transliteration(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(get_owned(db, Transliteration, item_id, user))
    db.commit()
    return Response(status_code=204)
