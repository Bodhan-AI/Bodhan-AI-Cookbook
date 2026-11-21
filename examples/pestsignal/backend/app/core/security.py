import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional
import jwt
from app.config import settings

def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a unique random salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100_000
    ).hex()
    return f"{salt}${key}"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against the stored salt$key hash."""
    try:
        salt, key = hashed_password.split("$")
        check_key = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000
        ).hex()
        return secrets.compare_digest(key, check_key)
    except Exception:
        return False

def create_access_token(data: dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Generate a JWT token with user id, email, role, and expiration."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None
