"""System routes — health check, AI status, audit logs."""

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role
from backend.core.constants import UserRole
from backend.models.audit import AuditLog
from backend.models.user import User
from backend.schemas.common import HealthResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Check the health of all dependencies."""
    # Check database
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # Check Redis
    redis_status = "ok"
    try:
        import redis.asyncio as redis_lib
        from backend.core.config import get_settings
        settings = get_settings()
        r = redis_lib.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
    except Exception:
        redis_status = "error"

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return HealthResponse(status=overall, database=db_status, redis=redis_status)


@router.get(
    "/ai/status",
    summary="AI pipeline readiness check",
)
async def ai_status(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Check if AI models are loaded and ready."""
    from backend.services.ai_pipeline import ai_pipeline

    return ai_pipeline.readiness()


@router.get(
    "/audit-logs",
    summary="View audit logs (admin only)",
)
async def list_audit_logs(
    skip: int = 0,
    limit: int = 100,
    action: str | None = None,
    resource: str | None = None,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List audit logs with optional filters."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    if action:
        query = query.where(AuditLog.action == action)
    if resource:
        query = query.where(AuditLog.resource == resource)
    result = await db.execute(query)
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource": log.resource,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
