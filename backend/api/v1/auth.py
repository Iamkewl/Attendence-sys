"""Authentication routes — register, login, refresh, logout."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.user import User
from backend.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from backend.schemas.common import MessageResponse
from backend.services.auth_service import (
    AuthError,
    authenticate_user,
    create_tokens,
    refresh_tokens,
    register_user,
    revoke_all_tokens,
)
from backend.services.redis_service import (
    cache_session,
    check_rate_limit,
    invalidate_session,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user account and return JWT tokens."""
    try:
        user = await register_user(db, email=body.email, password=body.password, role=body.role)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": e.code, "message": e.message},
        )

    tokens = await create_tokens(db, user)

    # Cache session
    await cache_session(user.id, {"role": user.role, "email": user.email})

    return tokens


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user and return JWT access + refresh tokens."""
    # Rate limiting
    allowed = await check_rate_limit(f"login:{body.email}", max_requests=5, window_seconds=900)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "RATE_LIMITED", "message": "Too many login attempts. Try again later."},
        )

    try:
        user = await authenticate_user(db, email=body.email, password=body.password)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": e.code, "message": e.message},
        )

    tokens = await create_tokens(db, user)

    # Cache session
    await cache_session(user.id, {"role": user.role, "email": user.email})

    return tokens


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Rotate refresh token and issue new access + refresh token pair."""
    try:
        tokens = await refresh_tokens(db, body.refresh_token)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": e.code, "message": e.message},
        )
    return tokens


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Logout (revoke all refresh tokens)",
)
async def logout(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke all refresh tokens for the current user."""
    await revoke_all_tokens(db, current_user.id)
    await invalidate_session(current_user.id)
    return {"message": "Logged out successfully"}
