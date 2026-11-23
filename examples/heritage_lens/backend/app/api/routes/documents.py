from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_file_store, get_owned, get_settings_dep, read_upload
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import DocumentType, HeritageStory, HistoricalDocument, User
from app.schemas.common import Page
from app.schemas.media import DocumentOut, DocumentTextEdit, document_out
from app.services.documents.pdf import pdf_page_count
from app.services.storage.validators import DOCUMENT, validate_upload

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    story_id: int | None = Form(default=None),
    document_type: DocumentType = Form(default=DocumentType.other),
    description: str | None = Form(default=None, max_length=5000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    store=Depends(get_file_store),
):
    """Stores the original scan untouched. OCR is a separate step (POST /api/ai/ocr)."""
    if story_id is not None:
        get_owned(db, HeritageStory, story_id, user)
    data = await read_upload(file, settings.max_document_bytes)
    valid = validate_upload(DOCUMENT, file.filename, file.content_type, data, settings.max_document_bytes)
    pages = 1
    if valid.sniffed_type == "pdf":
        pages = await asyncio.to_thread(pdf_page_count, data)
    stored = store.save_bytes(user_id=user.id, category="documents", data=data, extension=valid.extension)
    doc = HistoricalDocument(user_id=user.id, story_id=story_id, original_filename=valid.safe_name,
                             stored_path=stored.relative_path, mime_type=valid.mime_type, size_bytes=stored.size,
                             sha256=stored.sha256, page_count=pages, document_type=document_type,
                             description=description)
    db.add(doc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        store.delete(stored.relative_path)
        raise
    return document_out(doc)


@router.get("", response_model=Page[DocumentOut])
def list_documents(q: str | None = Query(default=None, max_length=100), story_id: int | None = None,
                   page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(HistoricalDocument).where(HistoricalDocument.user_id == user.id)
    if story_id is not None:
        stmt = stmt.where(HistoricalDocument.story_id == story_id)
    if q:
        stmt = stmt.where(HistoricalDocument.original_filename.contains(q, autoescape=True)
                          | HistoricalDocument.extracted_text.contains(q, autoescape=True)
                          | HistoricalDocument.extracted_edited_text.contains(q, autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(HistoricalDocument.created_at.desc(), HistoricalDocument.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page[DocumentOut](items=[document_out(r) for r in rows], total=total, page=page,
                             page_size=page_size)


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return document_out(get_owned(db, HistoricalDocument, document_id, user))


@router.get("/{document_id}/file")
def download_original(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                      store=Depends(get_file_store)):
    doc = get_owned(db, HistoricalDocument, document_id, user)
    path = store.path_for(doc.stored_path)
    if not path.is_file():
        raise NotFoundError("The original file is missing from storage.")
    return FileResponse(path, media_type=doc.mime_type, filename=doc.original_filename,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.put("/{document_id}/text", response_model=DocumentOut)
def edit_text(document_id: int, body: DocumentTextEdit, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Save corrected text. The raw OCR output and the original file are untouched."""
    doc = get_owned(db, HistoricalDocument, document_id, user)
    doc.extracted_edited_text = body.extracted_edited_text
    db.commit()
    return document_out(doc)


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                    store=Depends(get_file_store)):
    doc = get_owned(db, HistoricalDocument, document_id, user)
    path = doc.stored_path
    db.delete(doc)
    db.commit()
    store.delete(path)
    return Response(status_code=204)
