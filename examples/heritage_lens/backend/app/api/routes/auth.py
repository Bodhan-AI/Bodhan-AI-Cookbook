from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings_dep
from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError
from app.core.security import create_access_token, hash_password, verify_password
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: User, settings: Settings) -> TokenResponse:
    token, expires_in = create_access_token(user.id, secret=settings.secret_key, algorithm=settings.jwt_algorithm,
                                            expires_minutes=settings.access_token_expire_minutes)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    email = body.email.lower()
    taken = db.scalar(select(User.id).where(
        (func.lower(User.email) == email) | (func.lower(User.username) == body.username.lower())))
    if taken:
        raise ConflictError("That username or email is already registered.", code="already_registered")
    user = User(username=body.username, email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError("That username or email is already registered.", code="already_registered")
    return _token_for(user, settings)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)):
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    ok = verify_password(body.password, user.password_hash if user else None)
    if not user or not ok or not user.is_active:
        raise AuthenticationError("Incorrect email or password.", code="invalid_credentials")
    return _token_for(user, settings)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.put("/me", response_model=UserOut)
def update_me(body: UserUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    return user
