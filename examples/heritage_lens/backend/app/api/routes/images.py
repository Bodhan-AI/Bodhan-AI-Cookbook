from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

from app.api.deps import (
    get_current_user, get_db, get_file_store, get_optional_user, get_owned, get_settings_dep, media_visible, read_upload,
)
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import HeritageImage, HeritageStory, User
from app.schemas.common import Page
from app.services.storage.validators import IMAGE, validate_upload

router = APIRouter(prefix="/images", tags=["images"])


class ImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    story_id: int | None
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    caption: str | None
    attribution: str | None
    created_at: datetime
    provenance: str = "user_authored"


class ImageEdit(BaseModel):
    caption: str | None = Field(default=None, max_length=500)
    attribution: str | None = Field(default=None, max_length=300)


@router.post("", response_model=ImageOut, status_code=201)
async def upload_image(file: UploadFile = File(...), story_id: int | None = Form(default=None),
                       caption: str | None = Form(default=None, max_length=500),
                       attribution: str | None = Form(default=None, max_length=300),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db),
                       settings: Settings = Depends(get_settings_dep), store=Depends(get_file_store)):
    if story_id is not None:
        get_owned(db, HeritageStory, story_id, user)
    data = await read_upload(file, settings.max_image_bytes)
    valid = validate_upload(IMAGE, file.filename, file.content_type, data, settings.max_image_bytes)
    stored = store.save_bytes(user_id=user.id, category="images", data=data, extension=valid.extension)
    img = HeritageImage(user_id=user.id, story_id=story_id, image_path=stored.relative_path,
                        original_filename=valid.safe_name, mime_type=valid.mime_type, size_bytes=stored.size,
                        sha256=stored.sha256, caption=caption, attribution=attribution)
    db.add(img)
    try:
        db.commit()
    except Exception:
        db.rollback()
        store.delete(stored.relative_path)
        raise
    return img


@router.get("", response_model=Page[ImageOut])
def list_images(story_id: int | None = None, page: int = Query(default=1, ge=1),
                page_size: int = Query(default=50, ge=1, le=100), user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    stmt = select(HeritageImage).where(HeritageImage.user_id == user.id)
    if story_id is not None:
        stmt = stmt.where(HeritageImage.story_id == story_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(HeritageImage.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[ImageOut](items=[ImageOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{image_id}/file")
def image_file(image_id: int, user: User | None = Depends(get_optional_user), db: Session = Depends(get_db),
               store=Depends(get_file_store)):
    img = db.get(HeritageImage, image_id)
    if img is None or not media_visible(db, img, user):
        raise NotFoundError()
    path = store.path_for(img.image_path)
    if not path.is_file():
        raise NotFoundError("The image file is missing from storage.")
    return FileResponse(path, media_type=img.mime_type or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.put("/{image_id}", response_model=ImageOut)
def edit_image(image_id: int, body: ImageEdit, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    img = get_owned(db, HeritageImage, image_id, user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(img, k, v)
    db.commit()
    return img


@router.delete("/{image_id}", status_code=204)
def delete_image(image_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                 store=Depends(get_file_store)):
    img = get_owned(db, HeritageImage, image_id, user)
    path = img.image_path
    db.delete(img)
    db.commit()
    store.delete(path)
    return Response(status_code=204)
