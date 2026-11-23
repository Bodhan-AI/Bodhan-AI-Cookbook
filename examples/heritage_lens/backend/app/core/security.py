"""Password hashing (bcrypt) and JWT access tokens (PyJWT)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.errors import AuthenticationError

BCRYPT_MAX_BYTES = 72
_dummy_hash: bytes | None = None


def hash_password(password: str) -> str:
    import bcrypt

    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError("Password is longer than 72 bytes.")  # bcrypt would silently truncate
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time even for unknown users (pass password_hash=None)."""
    import bcrypt

    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt(rounds=12))
    raw = password.encode("utf-8")[:BCRYPT_MAX_BYTES]
    if not password_hash:
        bcrypt.checkpw(raw, _dummy_hash)
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except ValueError:
        return False


def create_access_token(user_id: int, *, secret: str, algorithm: str = "HS256",
                        expires_minutes: int = 720) -> tuple[str, int]:
    import jwt

    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=expires_minutes)
    token = jwt.encode({"sub": str(user_id), "iat": int(now.timestamp()), "exp": int(exp.timestamp()),
                        "typ": "access"}, secret, algorithm=algorithm)
    return token, expires_minutes * 60


def decode_access_token(token: str, *, secret: str, algorithm: str = "HS256") -> int:
    import jwt

    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm], options={"require": ["exp", "sub"]})
        if payload.get("typ") != "access":
            raise AuthenticationError("Invalid token.")
        return int(payload["sub"])
    except AuthenticationError:
        raise
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise AuthenticationError("Your session is invalid or has expired. Please sign in again.",
                                  headers={"WWW-Authenticate": "Bearer"}) from exc
