from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models import (
    AudioKind, AudioRecording, CapsuleStatus, HeritageStory, HistoricalDocument, OcrStatus, Privacy,
    PublicationStatus, SavedStory, TimeCapsule, Translation, Transliteration, User,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _count(db: Session, model, *conds) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0


@router.get("")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Counts and recent activity computed live from the caller's own rows. Nothing is estimated."""
    uid = user.id
    S = HeritageStory
    by_cat = db.execute(select(S.category, func.count()).where(S.user_id == uid).group_by(S.category)).all()
    seconds = db.scalar(select(func.coalesce(func.sum(AudioRecording.duration), 0.0)).where(
        AudioRecording.user_id == uid, AudioRecording.kind == AudioKind.original)) or 0.0
    activity = []
    for model, kind, label in ((S, "story", "title"), (TimeCapsule, "time_capsule", "title")):
        for r in db.scalars(select(model).where(model.user_id == uid).order_by(model.created_at.desc()).limit(8)):
            activity.append({"type": kind, "id": r.id, "label": getattr(r, label), "at": r.created_at})
    for r in db.scalars(select(HistoricalDocument).where(HistoricalDocument.user_id == uid)
                        .order_by(HistoricalDocument.created_at.desc()).limit(8)):
        activity.append({"type": "document", "id": r.id, "label": r.original_filename, "at": r.created_at})
    for r in db.scalars(select(AudioRecording).where(AudioRecording.user_id == uid)
                        .order_by(AudioRecording.created_at.desc()).limit(8)):
        activity.append({"type": f"audio_{r.kind.value}", "id": r.id,
                         "label": r.original_filename or f"Narration #{r.id}", "at": r.created_at})
    for r in db.scalars(select(Translation).where(Translation.user_id == uid)
                        .order_by(Translation.created_at.desc()).limit(8)):
        activity.append({"type": "translation", "id": r.id, "label": f"Translation to {r.target_language}", "at": r.created_at})
    activity.sort(key=lambda a: a["at"], reverse=True)
    return {
        "stories": {"total": _count(db, S, S.user_id == uid),
                    "drafts": _count(db, S, S.user_id == uid, S.publication_status == PublicationStatus.draft),
                    "published": _count(db, S, S.user_id == uid, S.publication_status == PublicationStatus.published),
                    "public": _count(db, S, S.user_id == uid, S.privacy == Privacy.public,
                                     S.publication_status == PublicationStatus.published),
                    "private": _count(db, S, S.user_id == uid, S.privacy == Privacy.private)},
        "by_category": [{"category": c.value if c else "uncategorised", "count": n} for c, n in by_cat],
        "documents": {"total": _count(db, HistoricalDocument, HistoricalDocument.user_id == uid),
                      "digitized": _count(db, HistoricalDocument, HistoricalDocument.user_id == uid,
                                          HistoricalDocument.ocr_status.in_([OcrStatus.completed, OcrStatus.partial]))},
        "audio": {"original": _count(db, AudioRecording, AudioRecording.user_id == uid, AudioRecording.kind == AudioKind.original),
                  "generated": _count(db, AudioRecording, AudioRecording.user_id == uid, AudioRecording.kind == AudioKind.generated),
                  "original_seconds": round(float(seconds), 1)},
        "translations": _count(db, Translation, Translation.user_id == uid),
        "transliterations": _count(db, Transliteration, Transliteration.user_id == uid),
        "saved": _count(db, SavedStory, SavedStory.user_id == uid),
        "time_capsules": {"total": _count(db, TimeCapsule, TimeCapsule.user_id == uid),
                          "sealed": _count(db, TimeCapsule, TimeCapsule.user_id == uid, TimeCapsule.status == CapsuleStatus.sealed)},
        "recent_activity": activity[:12],
    }
