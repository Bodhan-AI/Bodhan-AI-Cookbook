"""Authenticated endpoints that call the five Bodhan services.

Every response here is real Bodhan output (provenance="live_bodhan"). If a service is not configured
or fails, the caller gets an explicit error; there is no demo fallback.
"""
from __future__ import annotations

import io
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_bodhan, get_current_user, get_db, get_file_store, get_owned, get_settings_dep
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ValidationFailed
from app.models import (
    AudioKind, AudioRecording, HeritageStory, HistoricalDocument, OcrStatus, Translation, Transliteration, User,
)
from app.schemas.ai import (
    OcrRequest, SegmentOut, SpeakRequest, TranscribeRequest, TranslateRequest, TranslateResponse,
    TranslationOut, TransliterateRequest, TransliterateResponse, TransliterationOut,
)
from app.schemas.media import AudioOut, DocumentOut, document_out
from app.services.audio.processing import ffmpeg_version
from app.services.bodhan import capabilities
from app.services.bodhan.ocr import ocr_document
from app.services.bodhan.speech import synthesize_speech
from app.services.bodhan.transcription import transcribe_file
from app.services.bodhan.translation import translate_text
from app.services.bodhan.transliteration import transliterate_text

router = APIRouter(prefix="/ai", tags=["bodhan"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/capabilities")
def get_capabilities(settings: Settings = Depends(get_settings_dep)):
    """Per-service languages, voices, scripts and limits so the UI can disable unsupported options.
    `configured` means a key is present on the server, not that the service has been verified."""
    return capabilities.build_capabilities(settings.configured_services(),
                                           ffmpeg_available=ffmpeg_version(settings.ffmpeg_path) is not None)


@router.post("/transcribe", response_model=AudioOut)
async def transcribe(body: TranscribeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings_dep), store=Depends(get_file_store),
                     client=Depends(get_bodhan)):
    rec = get_owned(db, AudioRecording, body.audio_id, user)
    if not rec.original_audio_path:
        raise ValidationFailed("Only original recordings can be transcribed.", code="not_original_audio")
    if rec.transcript and not body.force:
        raise ConflictError("This recording already has a transcript. Pass force=true to run it again; "
                            "your edited transcript is kept either way.", code="transcript_exists")
    path = store.path_for(rec.original_audio_path)
    if not path.is_file():
        raise NotFoundError("The original recording is missing from storage.")
    result = await transcribe_file(
        client, path, body.language, ffmpeg_path=settings.ffmpeg_path,
        max_segment_seconds=settings.transcribe_segment_seconds, max_total_seconds=settings.max_audio_seconds,
        ffmpeg_timeout=settings.ffmpeg_timeout_seconds)
    rec.transcript = result.text           # machine output; original audio and any edit are untouched
    rec.transcript_language = body.language
    rec.transcript_segments = [asdict(s) for s in result.segments]
    rec.transcript_provider = "bodhan:indic-transcribe"
    rec.transcribed_at = _now()
    if not rec.language:
        rec.language = body.language
    db.commit()
    return AudioOut.from_model(rec)


@router.post("/translate", response_model=TranslateResponse)
async def translate(body: TranslateRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                    settings: Settings = Depends(get_settings_dep), client=Depends(get_bodhan)):
    story = get_owned(db, HeritageStory, body.story_id, user) if body.story_id is not None else None
    text = body.text if body.text and body.text.strip() else (story.original_text if story else None)
    if not text or not text.strip():
        raise ValidationFailed("There is no text to translate. The story has no original text yet.",
                               code="empty_text")
    result = await translate_text(client, text, body.target_language, script=body.script,
                                  max_chunk_chars=settings.translate_chunk_chars)
    saved = None
    if body.save and story is not None:
        saved = Translation(user_id=user.id, story_id=story.id, source_language=story.language,
                            target_language=body.target_language, script=body.script, original_text=text,
                            translated_text=result.translation, truncated=result.truncated,
                            segments=[{"index": s.index, "source_text": s.source_text,
                                       "translated_text": s.translated_text, "truncated": s.truncated}
                                      for s in result.segments])
        db.add(saved)
        db.commit()
    return TranslateResponse(
        translation=result.translation, target_language=result.target_language, script=result.script,
        truncated=result.truncated,
        segments=[SegmentOut(index=s.index, source_text=s.source_text, result_text=s.translated_text,
                             truncated=s.truncated) for s in result.segments],
        saved=TranslationOut.model_validate(saved) if saved else None)


@router.post("/transliterate", response_model=TransliterateResponse)
async def transliterate(body: TransliterateRequest, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
                        client=Depends(get_bodhan)):
    if body.story_id is not None:
        get_owned(db, HeritageStory, body.story_id, user)
    result = await transliterate_text(client, body.text, body.language, script=body.script,
                                      max_chunk_chars=settings.transliterate_chunk_chars)
    saved = None
    if body.save:
        saved = Transliteration(user_id=user.id, story_id=body.story_id, language=body.language,
                                source_text=body.text, target_script=body.script,
                                converted_text=result.converted_text, truncated=result.truncated,
                                segments=[{"index": s.index, "source_text": s.source_text,
                                           "converted_text": s.converted_text, "truncated": s.truncated}
                                          for s in result.segments])
        db.add(saved)
        db.commit()
    return TransliterateResponse(
        converted_text=result.converted_text, language=result.language, script=result.script,
        truncated=result.truncated,
        segments=[SegmentOut(index=s.index, source_text=s.source_text, result_text=s.converted_text,
                             truncated=s.truncated) for s in result.segments],
        saved=TransliterationOut.model_validate(saved) if saved else None)


@router.post("/ocr", response_model=DocumentOut)
async def ocr(body: OcrRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings_dep), store=Depends(get_file_store),
              client=Depends(get_bodhan)):
    doc = get_owned(db, HistoricalDocument, body.document_id, user)
    if doc.ocr_status in (OcrStatus.completed, OcrStatus.partial) and not body.force:
        raise ConflictError("This document has already been processed. Pass force=true to run OCR again; "
                            "your edited text is kept either way.", code="ocr_exists")
    path = store.path_for(doc.stored_path)
    if not path.is_file():
        raise NotFoundError("The original file is missing from storage.")
    result = await ocr_document(client, path.read_bytes(), table_format=body.table_format,
                                max_tokens=body.max_tokens, max_tokens_cap=settings.ocr_max_tokens_cap,
                                pdf_dpi=settings.ocr_pdf_dpi, max_pdf_pages=settings.ocr_max_pdf_pages)
    doc.ocr_pages = [asdict(p) for p in result.pages]  # retains page numbers, order, blocks and any errors
    doc.ocr_provider = "bodhan:indic-ocr"
    doc.ocr_at = _now()
    if result.complete:
        doc.ocr_status, doc.ocr_error = OcrStatus.completed, None
        doc.extracted_text = result.combined_markdown
    elif len(result.failed_pages) < len(result.pages):
        doc.ocr_status = OcrStatus.partial
        doc.ocr_error = f"Pages that could not be read: {', '.join(map(str, result.failed_pages))}"
        doc.extracted_text = result.combined_markdown
    else:
        doc.ocr_status, doc.ocr_error = OcrStatus.failed, "No page could be read."
        db.commit()
        raise AppError("The document could not be read by the OCR service.", status_code=502,
                       code="ocr_failed", details={"failed_pages": result.failed_pages})
    db.commit()
    return document_out(doc)


async def _resolve_speech_text(body: SpeakRequest, user: User, db: Session):
    story = get_owned(db, HeritageStory, body.story_id, user) if body.story_id is not None else None
    translation = None
    if body.translation_id is not None:
        translation = get_owned(db, Translation, body.translation_id, user)
        if story is None:
            story = db.get(HeritageStory, translation.story_id)
    if body.text and body.text.strip():
        text = body.text
    elif translation is not None:
        text = translation.edited_text or translation.translated_text
    elif story is not None and story.original_text:
        text = story.original_text
    else:
        raise ValidationFailed("There is no text to read aloud.", code="empty_text")
    return story, translation, text


@router.post("/speak", response_model=AudioOut, status_code=201)
async def speak(body: SpeakRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings_dep), store=Depends(get_file_store),
                client=Depends(get_bodhan)):
    """Generate narration, store it as a separate 'generated' recording linked to the story."""
    story, translation, text = await _resolve_speech_text(body, user, db)
    if len(text) > 5000:
        raise ValidationFailed("Narration is limited to 5,000 characters per request.", code="text_too_long")
    result = await synthesize_speech(client, text, body.language, body.voice, style=body.style,
                                     max_chunk_chars=settings.speak_chunk_chars, gap_ms=settings.speak_gap_ms)
    stored = store.save_bytes(user_id=user.id, category="generated_audio", data=result.wav_bytes, extension=".wav")
    rec = AudioRecording(user_id=user.id, story_id=story.id if story else None, kind=AudioKind.generated,
                         generated_audio_path=stored.relative_path, mime_type="audio/wav", size_bytes=stored.size,
                         sha256=stored.sha256, language=body.language, duration=round(result.duration_seconds, 3),
                         narrated_text=text, narration_voice=result.voice, narration_style=result.style,
                         translation_id=translation.id if translation else None)
    db.add(rec)
    try:
        db.commit()
    except Exception:
        db.rollback()
        store.delete(stored.relative_path)
        raise
    return AudioOut.from_model(rec)


@router.post("/speak/preview")
async def speak_preview(body: SpeakRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db),
                        settings: Settings = Depends(get_settings_dep), client=Depends(get_bodhan)):
    """Return WAV bytes directly without saving anything."""
    _, _, text = await _resolve_speech_text(body, user, db)
    if len(text) > 5000:
        raise ValidationFailed("Preview is limited to 5,000 characters.", code="text_too_long")
    result = await synthesize_speech(client, text, body.language, body.voice, style=body.style,
                                     max_chunk_chars=settings.speak_chunk_chars, gap_ms=settings.speak_gap_ms)
    return Response(content=result.wav_bytes, media_type="audio/wav",
                    headers={"X-Audio-Duration-Seconds": f"{result.duration_seconds:.3f}",
                             "Cache-Control": "private, no-store"})
