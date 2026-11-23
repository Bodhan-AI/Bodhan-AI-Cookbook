from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_current_user, get_db, get_file_store, get_optional_user, get_owned, get_settings_dep, media_visible, read_upload,
)
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import AudioKind, AudioRecording, HeritageStory, User
from app.schemas.common import Page
from app.schemas.media import AudioOut, TranscriptEdit
from app.services.audio.processing import probe_duration_seconds
from app.services.storage.validators import AUDIO, validate_upload

router = APIRouter(prefix="/audio", tags=["audio"])


@router.post("", response_model=AudioOut, status_code=201)
async def upload_audio(
    file: UploadFile = File(...),
    story_id: int | None = Form(default=None),
    language: str | None = Form(default=None, pattern=r"^[A-Za-z]{2,3}$"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    store=Depends(get_file_store),
):
    if story_id is not None:
        get_owned(db, HeritageStory, story_id, user)
    data = await read_upload(file, settings.max_audio_bytes)
    valid = validate_upload(AUDIO, file.filename, file.content_type, data, settings.max_audio_bytes)

    # Prove the file really decodes (and measure it) before keeping it. The original bytes are stored as-is.
    with tempfile.TemporaryDirectory(prefix="hl_up_") as tmp:
        probe = Path(tmp) / f"probe{valid.extension}"
        probe.write_bytes(data)
        duration = await asyncio.to_thread(probe_duration_seconds, probe, ffmpeg_path=settings.ffmpeg_path,
                                           timeout=settings.ffmpeg_timeout_seconds)
    stored = store.save_bytes(user_id=user.id, category="audio", data=data, extension=valid.extension)
    rec = AudioRecording(user_id=user.id, story_id=story_id, kind=AudioKind.original,
                         original_audio_path=stored.relative_path, original_filename=valid.safe_name,
                         mime_type=valid.mime_type, size_bytes=stored.size, sha256=stored.sha256,
                         language=language, duration=round(duration, 3))
    db.add(rec)
    try:
        db.commit()
    except Exception:
        db.rollback()
        store.delete(stored.relative_path)
        raise
    return AudioOut.from_model(rec)


@router.get("", response_model=Page[AudioOut])
def list_audio(story_id: int | None = None, kind: AudioKind | None = None,
               page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from sqlalchemy import func

    stmt = select(AudioRecording).where(AudioRecording.user_id == user.id)
    if story_id is not None:
        stmt = stmt.where(AudioRecording.story_id == story_id)
    if kind is not None:
        stmt = stmt.where(AudioRecording.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AudioRecording.created_at.desc(), AudioRecording.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[AudioOut](items=[AudioOut.from_model(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{audio_id}", response_model=AudioOut)
def get_audio(audio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return AudioOut.from_model(get_owned(db, AudioRecording, audio_id, user))


@router.get("/{audio_id}/file")
def download_audio(audio_id: int, which: str = Query(default="auto", pattern="^(auto|original|generated)$"),
                   user: User | None = Depends(get_optional_user), db: Session = Depends(get_db),
                   store=Depends(get_file_store)):
    rec = db.get(AudioRecording, audio_id)
    if rec is None or not media_visible(db, rec, user):
        raise NotFoundError()
    rel = {"original": rec.original_audio_path, "generated": rec.generated_audio_path,
           "auto": rec.original_audio_path or rec.generated_audio_path}[which]
    if not rel:
        raise NotFoundError("This recording has no such audio file.")
    path = store.path_for(rel)
    if not path.is_file():
        raise NotFoundError("The audio file is missing from storage.")
    media_type = "audio/wav" if rel == rec.generated_audio_path else (rec.mime_type or "application/octet-stream")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-store",
                                                              "X-Content-Type-Options": "nosniff"})


@router.put("/{audio_id}/transcript", response_model=AudioOut)
def edit_transcript(audio_id: int, body: TranscriptEdit, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Save the contributor's corrections. The raw machine transcript is left untouched."""
    rec = get_owned(db, AudioRecording, audio_id, user)
    rec.transcript_edited = body.transcript_edited
    db.commit()
    return AudioOut.from_model(rec)


@router.delete("/{audio_id}", status_code=204)
def delete_audio(audio_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                 store=Depends(get_file_store)):
    rec = get_owned(db, AudioRecording, audio_id, user)
    paths = [rec.original_audio_path, rec.generated_audio_path]
    db.delete(rec)
    db.commit()
    for p in paths:
        store.delete(p)
    return Response(status_code=204)
