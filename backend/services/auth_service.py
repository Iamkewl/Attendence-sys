"""Authentication service — register, login, refresh, logout.

Handles all auth business logic, delegating crypto to core.security.
Stores refresh tokens in PostgreSQL with rotation on every refresh.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.constants import ErrorCode
from backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from backend.models.user import RefreshToken, User


class AuthError(Exception):
    """Authentication/authorization failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _hash_token(token: str) -> str:
    """SHA-256 hash a refresh token for safe DB storage."""
    return hashlib.sha256(token.encode()).hexdigest()


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    role: str = "student",
) -> User:
    """Register a new user with Argon2id-hashed password.

    Raises:
        AuthError: If email already exists.
    """
    # Check for existing email
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise AuthError(ErrorCode.EMAIL_ALREADY_EXISTS, "Email already registered")

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def authenticate_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
) -> User:
    """Authenticate user by email + password.

    Raises:
        AuthError: If credentials are invalid or account is inactive.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        raise AuthError(ErrorCode.INVALID_CREDENTIALS, "Invalid email or password")

    if not user.is_active:
        raise AuthError(ErrorCode.ACCOUNT_INACTIVE, "Account is deactivated")

    return user


async def create_tokens(db: AsyncSession, user: User) -> dict:
    """Create access + refresh token pair for a user.

    Stores the refresh token hash in the database for later validation.
    """
    access_token = create_access_token(user.id, user.role)
    refresh_token_str, expires_at = create_refresh_token(user.id)

    # Store refresh token hash
    rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_token(refresh_token_str),
        expires_at=expires_at,
    )
    db.add(rt)
    await db.flush()

    from backend.core.config import get_settings
    settings = get_settings()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
    }


async def refresh_tokens(db: AsyncSession, refresh_token_str: str) -> dict:
    """Rotate refresh token — revoke old, issue new pair.

    Implements refresh token rotation per OWASP guidelines.

    Raises:
        AuthError: If token is invalid, expired, or already revoked.
    """
    # Decode the refresh token
    try:
        payload = decode_token(refresh_token_str)
    except Exception:
        raise AuthError(ErrorCode.TOKEN_INVALID, "Invalid refresh token")

    if payload.get("type") != "refresh":
        raise AuthError(ErrorCode.TOKEN_INVALID, "Not a refresh token")

    user_id = int(payload["sub"])
    token_hash = _hash_token(refresh_token_str)

    # Find the stored refresh token
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token = result.scalar_one_or_none()

    if not stored_token:
        raise AuthError(ErrorCode.TOKEN_INVALID, "Refresh token not found")

    if stored_token.revoked:
        raise AuthError(ErrorCode.TOKEN_REVOKED, "Refresh token already revoked")

    if stored_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise AuthError(ErrorCode.TOKEN_EXPIRED, "Refresh token expired")

    # Revoke the old token
    stored_token.revoked = True

    # Get the user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise AuthError(ErrorCode.ACCOUNT_INACTIVE, "Account not found or inactive")

    # Issue new tokens
    return await create_tokens(db, user)


async def revoke_all_tokens(db: AsyncSession, user_id: int) -> None:
    """Revoke all refresh tokens for a user (logout everywhere)."""
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked == False,  # noqa: E712
        )
    )
    for token in result.scalars().all():
        token.revoked = True
