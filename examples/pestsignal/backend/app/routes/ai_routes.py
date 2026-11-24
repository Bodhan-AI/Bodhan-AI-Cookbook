import os
import uuid
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from pydantic import BaseModel
from app.config import settings
from app.services.bodhan_service import bodhan_service, SUPPORTED_LANGUAGES
from app.core.dependencies import get_current_user
from app.models.user import User

router = APIRouter(prefix="/ai", tags=["AI Integration (Bodhan AI)"])

class TranslateRequest(BaseModel):
    text: str
    source_language: str = "te"
    target_language: str = "en"

class SpeakRequest(BaseModel):
    text: str
    language: str = "te"

@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user)
):
    """
    Accepts farmer's audio recording, validates audio data, saves to local storage,
    and calls Bodhan AI's indic-transcribe model.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Audio file must have a filename.")

    # Read audio bytes
    audio_bytes = await file.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")

    if len(audio_bytes) > settings.MAX_AUDIO_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Audio file exceeds maximum allowed size of {settings.MAX_AUDIO_SIZE_BYTES // (1024*1024)}MB."
        )

    # Determine extension and unique path
    ext = os.path.splitext(file.filename)[1].lower() or ".wav"
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    saved_path = os.path.join(settings.UPLOAD_DIR, unique_filename)

    with open(saved_path, "wb") as f:
        f.write(audio_bytes)

    # Call Bodhan AI Indic-Transcribe
    result = bodhan_service.transcribe_audio(
        audio_bytes=audio_bytes,
        filename=unique_filename,
        content_type=file.content_type or "audio/wav",
        language=language
    )

    audio_url = f"/uploads/audio/{unique_filename}"
    result["audio_file_path"] = audio_url

    if not result.get("success"):
        # Still return the audio_url and helpful message so user can retry or type manually
        return {
            "success": False,
            "error": result.get("error"),
            "details": result.get("details"),
            "audio_file_path": audio_url,
            "language": language
        }

    return result

@router.post("/translate")
def translate_observation(
    req: TranslateRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Translates farmer's observation from regional Indian language to English
    using Bodhan AI's indic-translate model.
    """
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text to translate cannot be empty.")

    result = bodhan_service.translate_text(
        text=req.text.strip(),
        source_language=req.source_language,
        target_language=req.target_language
    )
    return result

@router.get("/languages")
def get_supported_languages():
    """Returns verified supported languages for Bodhan AI."""
    return [
        {"code": code, "name": name}
        for code, name in SUPPORTED_LANGUAGES.items()
    ]
