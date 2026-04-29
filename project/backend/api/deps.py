"""FastAPI dependency injection — shared dependencies for all routes.

Provides:
- get_db(): Async database session
- get_current_user(): Extract and validate JWT from Authorization header
- require_role(): Role-based access control guard
"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.constants import ErrorCode, UserRole
from backend.core.security import decode_token
from backend.db.session import get_db as _get_db
from backend.models.user import User

# Re-export get_db from session module
get_db = _get_db

# Bearer token scheme for OpenAPI docs
_bearer_scheme = HTTPBearer(auto_error=False)

settings = get_settings()


async def _get_user_from_access_token(token: str, db: AsyncSession) -> User:
    """Validate an access token and return the corresponding active user."""
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.TOKEN_EXPIRED, "message": "Token has expired"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.TOKEN_INVALID, "message": "Invalid token"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.TOKEN_INVALID, "message": "Not an access token"},
        )

    user_id = int(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.TOKEN_INVALID, "message": "User not found"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": ErrorCode.ACCOUNT_INACTIVE, "message": "Account is deactivated"},
        )

    return user


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate JWT access token from the Authorization header.

    Returns the authenticated User object.

    Raises:
        HTTPException 401: Missing, expired, or invalid token.
        HTTPException 403: Account is inactive.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.TOKEN_INVALID, "message": "Missing authorization header"},
        )

    return await _get_user_from_access_token(credentials.credentials, db)


async def get_current_user_from_token(token: str, db: AsyncSession) -> User:
    """Validate a raw bearer token string and return the authenticated user."""
    return await _get_user_from_access_token(token, db)


def require_role(*allowed_roles: UserRole):
    """Create a dependency that enforces role-based access control.

    Usage::

        @router.get("/admin-only")
        async def admin_endpoint(
            user: User = Depends(require_role(UserRole.ADMIN))
        ):
            ...

        @router.get("/instructor-or-admin")
        async def multi_role_endpoint(
            user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR))
        ):
            ...
    """

    async def _role_guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if current_user.role not in [role.value for role in allowed_roles]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": ErrorCode.INSUFFICIENT_PERMISSIONS,
                    "message": f"Requires role: {', '.join(r.value for r in allowed_roles)}",
                },
            )
        return current_user

    return _role_guard
