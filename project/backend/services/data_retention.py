"""Data retention and right-to-deletion workflows for biometric compliance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.attendance import Detection, Snapshot
from backend.models.audit import AuditLog
from backend.models.student import Student, StudentEmbedding
from backend.services.audit_service import log_audit

settings = get_settings()


def _as_utc(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _retention_due_year(enrollment_year: int) -> int:
    return (
        int(enrollment_year)
        + int(settings.data_retention_default_program_years)
        + int(settings.data_retention_grace_years)
    )


def enforce_retention_sync(db: Session) -> dict[str, Any]:
    """Run nightly retention purge for templates and stale detection records."""
    if not settings.enable_data_retention:
        return {
            "status": "skipped",
            "reason": "enable_data_retention=false",
            "purged_embeddings": 0,
            "purged_detections": 0,
            "purged_snapshots": 0,
        }

    now = datetime.now(UTC)
    current_year = int(now.year)
    cutoff = now - timedelta(days=int(settings.detection_retention_days))

    due_student_ids = [
        int(row[0])
        for row in db.query(Student.id, Student.enrollment_year)
        .filter(Student.enrollment_year.is_not(None))
        .all()
        if _retention_due_year(int(row[1])) <= current_year
    ]

    purged_embeddings = 0
    if due_student_ids:
        purged_embeddings = (
            db.query(StudentEmbedding)
            .filter(StudentEmbedding.student_id.in_(due_student_ids))
            .delete(synchronize_session=False)
        )

    stale_snapshot_subq = (
        db.query(Snapshot.id)
        .filter(Snapshot.timestamp < cutoff)
        .subquery()
    )
    purged_detections = (
        db.query(Detection)
        .filter(Detection.snapshot_id.in_(select(stale_snapshot_subq.c.id)))
        .delete(synchronize_session=False)
    )

    purged_snapshots = (
        db.query(Snapshot)
        .filter(Snapshot.timestamp < cutoff)
        .delete(synchronize_session=False)
    )

    db.add(
        AuditLog(
            user_id=None,
            action="retention.nightly_purge",
            resource="compliance",
            details={
                "due_students": len(due_student_ids),
                "purged_embeddings": int(purged_embeddings),
                "purged_detections": int(purged_detections),
                "purged_snapshots": int(purged_snapshots),
                "cutoff": cutoff.isoformat(),
            },
        )
    )

    return {
        "status": "completed",
        "due_students": len(due_student_ids),
        "purged_embeddings": int(purged_embeddings),
        "purged_detections": int(purged_detections),
        "purged_snapshots": int(purged_snapshots),
        "cutoff": cutoff.isoformat(),
    }


async def get_retention_status_async(db: AsyncSession) -> dict[str, Any]:
    """Return dashboard retention status and pending record counts."""
    now = datetime.now(UTC)
    current_year = int(now.year)
    cutoff = now - timedelta(days=int(settings.detection_retention_days))

    students_rows = await db.execute(
        select(Student.id, Student.enrollment_year).where(Student.enrollment_year.is_not(None))
    )
    due_student_ids = [
        int(sid)
        for sid, enrollment_year in students_rows.all()
        if enrollment_year is not None and _retention_due_year(int(enrollment_year)) <= current_year
    ]

    pending_embeddings = 0
    if due_student_ids:
        pending_embeddings = int(
            (
                await db.execute(
                    select(func.count(StudentEmbedding.id)).where(
                        StudentEmbedding.student_id.in_(due_student_ids)
                    )
                )
            ).scalar_one()
            or 0
        )

    pending_old_snapshots = int(
        (
            await db.execute(
                select(func.count(Snapshot.id)).where(Snapshot.timestamp < cutoff)
            )
        ).scalar_one()
        or 0
    )

    next_purge_at = now.replace(
        hour=int(settings.retention_nightly_hour_utc),
        minute=0,
        second=0,
        microsecond=0,
    )
    if next_purge_at <= now:
        next_purge_at = next_purge_at + timedelta(days=1)

    return {
        "enabled": bool(settings.enable_data_retention),
        "next_purge_at": next_purge_at.isoformat(),
        "pending_student_count": len(due_student_ids),
        "pending_embedding_count": int(pending_embeddings),
        "pending_snapshot_count": int(pending_old_snapshots),
        "detection_retention_days": int(settings.detection_retention_days),
    }


async def delete_student_biometric_data_async(
    db: AsyncSession,
    *,
    student_id: int,
    actor_user_id: int | None,
) -> dict[str, Any]:
    """Right-to-deletion flow: remove templates and anonymize student identity."""
    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    if student is None:
        raise ValueError("Student not found")

    embedding_count = int(
        (
            await db.execute(
                select(func.count(StudentEmbedding.id)).where(StudentEmbedding.student_id == student_id)
            )
        ).scalar_one()
        or 0
    )

    detection_count = int(
        (
            await db.execute(
                select(func.count(Detection.id)).where(Detection.student_id == student_id)
            )
        ).scalar_one()
        or 0
    )

    await db.execute(
        delete(StudentEmbedding).where(StudentEmbedding.student_id == student_id)
    )

    # Anonymize detection records — set student_id to NULL so the FK is severed
    from sqlalchemy import update
    await db.execute(
        update(Detection)
        .where(Detection.student_id == student_id)
        .values(student_id=None)
    )

    student.user_id = None
    student.is_enrolled = False
    student.department = None
    student.enrollment_year = None
    student.name = f"Deleted Subject #{student_id}"

    await log_audit(
        db,
        user_id=actor_user_id,
        action="student.biometric_data.delete",
        resource="student_embeddings",
        details={
            "student_id": int(student_id),
            "removed_embedding_count": int(embedding_count),
            "anonymized_detection_count": int(detection_count),
        },
    )

    return {
        "student_id": int(student_id),
        "removed_embedding_count": int(embedding_count),
        "anonymized_detection_count": int(detection_count),
    }
