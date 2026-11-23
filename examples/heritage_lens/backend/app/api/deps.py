from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthenticationError, NotFoundError, PayloadTooLarge
from app.core.security import decode_access_token
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_file_store(request: Request):
    return request.app.state.file_store


def get_bodhan(request: Request):
    return request.app.state.bodhan


def _user_from_token(creds: HTTPAuthorizationCredentials | None, db: Session, settings: Settings) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise AuthenticationError("Please sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_access_token(creds.credentials, secret=settings.secret_key,
                                  algorithm=settings.jwt_algorithm)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Your session is invalid or has expired. Please sign in again.",
                                  headers={"WWW-Authenticate": "Bearer"})
    return user


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
                     db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> User:
    return _user_from_token(creds, db, settings)


def get_optional_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
                      db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> User | None:
    if creds is None:
        return None
    return _user_from_token(creds, db, settings)


def get_owned(db: Session, model, object_id: int, user: User):
    """Fetch a row the user owns. Others' rows return 404 (not 403) so existence is not leaked."""
    obj = db.get(model, object_id)
    if obj is None or getattr(obj, "user_id", None) != user.id:
        raise NotFoundError()
    return obj


async def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an upload without ever holding more than max_bytes + one chunk in memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLarge(f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def story_is_public(story) -> bool:
    from app.models import Privacy, PublicationStatus
    return story is not None and story.privacy == Privacy.public and story.publication_status == PublicationStatus.published


def media_visible(db: Session, obj, user: User | None) -> bool:
    """Owner always; anyone only when the media belongs to a published, public story."""
    from app.models import HeritageStory
    if user is not None and obj.user_id == user.id:
        return True
    if getattr(obj, "story_id", None) is None:
        return False
    return story_is_public(db.get(HeritageStory, obj.story_id))
