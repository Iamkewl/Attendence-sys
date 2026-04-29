"""Audit log service — logs all state-changing API operations."""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit import AuditLog


async def log_audit(
    db: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    resource: str,
    details: dict | None = None,
) -> None:
    """Write an audit log entry.

    Called by route handlers on POST/PUT/PATCH/DELETE operations.

    Args:
        db: Async database session.
        user_id: ID of the user performing the action (None for system actions).
        action: Action type (e.g., 'user.create', 'student.enroll').
        resource: Resource type (e.g., 'users', 'students').
        details: Optional JSONB payload with operation context.
    """
    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource=resource,
        details=details,
    )
    db.add(entry)
    await db.flush()
