"""JWT token management and Argon2id password hashing.

Provides:
- Argon2id password hashing (OWASP recommended)
- JWT access token creation (short-lived, 15m)
- JWT refresh token creation (long-lived, 7d, rotated)
- Token decode and validation
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from backend.core.config import get_settings

settings = get_settings()

# Argon2id hasher with OWASP-recommended parameters
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


# ── Password Hashing ─────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its Argon2id hash.

    Returns False on mismatch instead of raising.
    """
    try:
        return _hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Check if a hash needs to be upgraded (e.g., params changed)."""
    return _hasher.check_needs_rehash(hashed_password)


# ── JWT Token Management ─────────────────────────────────


def create_access_token(
    subject: int,
    role: str,
    *,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a short-lived JWT access token.

    Args:
        subject: User ID
        role: User role (admin, instructor, student, device)
        extra_claims: Additional JWT claims

    Returns:
        Encoded JWT string
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload = {
        "sub": str(subject),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": expire,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: int) -> tuple[str, datetime]:
    """Create a long-lived JWT refresh token.

    Args:
        subject: User ID

    Returns:
        Tuple of (encoded JWT string, expiration datetime)
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.jwt_refresh_token_expire_days)

    payload = {
        "sub": str(subject),
        "type": "refresh",
        "iat": now,
        "exp": expire,
    }

    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expire


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token.

    Raises:
        jwt.ExpiredSignatureError: Token has expired
        jwt.InvalidTokenError: Token is invalid
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
