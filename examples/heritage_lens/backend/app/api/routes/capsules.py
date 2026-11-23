from __future__ import annotations

import asyncio
import tempfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_file_store, get_owned, get_settings_dep, read_upload
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationFailed
from app.models import CapsuleStatus, Privacy, TimeCapsule, User
from app.schemas.common import Page
from app.services.audio.processing import probe_duration_seconds
from app.services.storage.validators import AUDIO, validate_upload

router = APIRouter(prefix="/time-capsules", tags=["time-capsules"])
DELIVERY_NOTE = ("Saved with an intended release date. HeritageLens does not yet send notifications or "
                 "release capsules automatically.")


class CapsuleIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str | None = Field(default=None, max_length=50_000)
    dedication: str | None = Field(default=None, max_length=200)
    intended_release_date: date | None = None
    privacy: Privacy = Privacy.private
    status: CapsuleStatus = CapsuleStatus.draft

    @model_validator(mode="after")
    def _sealed_needs_date(self):
        if self.status == CapsuleStatus.sealed and self.intended_release_date is None:
            raise ValueError("A sealed capsule needs an intended release date.")
        return self


class CapsuleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    message: str | None = Field(default=None, max_length=50_000)
    dedication: str | None = Field(default=None, max_length=200)
    intended_release_date: date | None = None
    privacy: Privacy | None = None
    status: CapsuleStatus | None = None


class CapsuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    message: str | None
    dedication: str | None
    intended_release_date: date | None
    privacy: Privacy
    status: CapsuleStatus
    has_audio: bool = False
    created_at: datetime
    updated_at: datetime
    provenance: str = "user_authored"
    delivery_note: str = DELIVERY_NOTE

    @classmethod
    def of(cls, c: TimeCapsule) -> "CapsuleOut":
        out = cls.model_validate(c)
        out.has_audio = bool(c.audio_path)
        return out


@router.post("", response_model=CapsuleOut, status_code=201)
def create_capsule(body: CapsuleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = TimeCapsule(user_id=user.id, **body.model_dump())
    db.add(c)
    db.commit()
    return CapsuleOut.of(c)


@router.get("", response_model=Page[CapsuleOut])
def list_capsules(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(TimeCapsule).where(TimeCapsule.user_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(TimeCapsule.created_at.desc(), TimeCapsule.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[CapsuleOut](items=[CapsuleOut.of(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{capsule_id}", response_model=CapsuleOut)
def get_capsule(capsule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return CapsuleOut.of(get_owned(db, TimeCapsule, capsule_id, user))


@router.put("/{capsule_id}", response_model=CapsuleOut)
def update_capsule(capsule_id: int, body: CapsuleUpdate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    c = get_owned(db, TimeCapsule, capsule_id, user)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "title" and v is None:
            raise ValidationFailed("Title cannot be empty.")
        setattr(c, k, v)
    if c.status == CapsuleStatus.sealed and c.intended_release_date is None:
        raise ValidationFailed("A sealed capsule needs an intended release date.")
    db.commit()
    return CapsuleOut.of(c)


@router.post("/{capsule_id}/audio", response_model=CapsuleOut)
async def attach_audio(capsule_id: int, file: UploadFile = File(...), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
                       store=Depends(get_file_store)):
    c = get_owned(db, TimeCapsule, capsule_id, user)
    data = await read_upload(file, settings.max_audio_bytes)
    valid = validate_upload(AUDIO, file.filename, file.content_type, data, settings.max_audio_bytes)
    with tempfile.TemporaryDirectory(prefix="hl_cap_") as tmp:
        probe = Path(tmp) / f"p{valid.extension}"
        probe.write_bytes(data)
        await asyncio.to_thread(probe_duration_seconds, probe, ffmpeg_path=settings.ffmpeg_path,
                                timeout=settings.ffmpeg_timeout_seconds)
    stored = store.save_bytes(user_id=user.id, category="capsules", data=data, extension=valid.extension)
    old, c.audio_path = c.audio_path, stored.relative_path
    db.commit()
    store.delete(old)
    return CapsuleOut.of(c)


@router.get("/{capsule_id}/audio")
def capsule_audio(capsule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                  store=Depends(get_file_store)):
    c = get_owned(db, TimeCapsule, capsule_id, user)
    if not c.audio_path:
        raise NotFoundError("This capsule has no voice message.")
    path = store.path_for(c.audio_path)
    if not path.is_file():
        raise NotFoundError("The voice message is missing from storage.")
    media = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac",
             ".webm": "audio/webm", ".m4a": "audio/mp4"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, no-store"})


@router.delete("/{capsule_id}", status_code=204)
def delete_capsule(capsule_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                   store=Depends(get_file_store)):
    c = get_owned(db, TimeCapsule, capsule_id, user)
    audio = c.audio_path
    db.delete(c)
    db.commit()
    store.delete(audio)
    return Response(status_code=204)
