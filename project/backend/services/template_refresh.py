"""Automatic template refresh and rollback logic for template lifecycle governance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import cv2
import numpy as np
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.governance import TemplateAuditLog
from backend.models.student import StudentEmbedding
from backend.services.ai_pipeline import ai_pipeline

settings = get_settings()


def _cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    a = np.asarray(vec_a, dtype=np.float32).flatten()
    b = np.asarray(vec_b, dtype=np.float32).flatten()
    if a.size == 0 or b.size == 0 or a.shape[0] != b.shape[0]:
        return 0.0
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
    return float(np.dot(a, b) / denom)


def _as_utc(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def maybe_auto_refresh_template_sync(
    db: Session,
    *,
    match: Any,
    image_bgr: np.ndarray,
    liveness_passed: bool,
    refreshed_by: str = "system",
) -> dict[str, Any] | None:
    """Refresh an old active template if confidence/quality/liveness gates pass."""
    if not settings.enable_auto_template_refresh:
        return None

    confidence = float(getattr(match, "confidence", 0.0) or 0.0)
    quality = float(getattr(match, "quality", 0.0) or 0.0)
    student_id = int(getattr(match, "student_id"))

    if confidence < float(settings.auto_refresh_min_confidence):
        return None
    if quality < float(settings.auto_refresh_min_quality):
        return None
    if not liveness_passed:
        return None

    bbox = getattr(match, "bbox", None)
    if not bbox or len(bbox) != 4:
        return None

    x, y, w, h = [int(v) for v in bbox]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(int(image_bgr.shape[1]), x + w)
    y2 = min(int(image_bgr.shape[0]), y + h)
    if x2 <= x1 or y2 <= y1:
        return None

    crop = image_bgr[y1:y2, x1:x2]
    if crop.size == 0:
        return None

    fresh_embedding = ai_pipeline.extract_embedding(crop)
    if fresh_embedding is None:
        return None

    fresh_embedding = np.asarray(fresh_embedding, dtype=np.float32).flatten()
    if fresh_embedding.size == 0 or not np.isfinite(fresh_embedding).all():
        return None

    active_templates = (
        db.query(StudentEmbedding)
        .filter(
            StudentEmbedding.student_id == student_id,
            StudentEmbedding.model_name == "arcface",
            StudentEmbedding.template_status == "active",
            StudentEmbedding.is_active.is_(True),
        )
        .order_by(desc(StudentEmbedding.created_at), desc(StudentEmbedding.id))
        .all()
    )
    if not active_templates:
        return None

    best_template: StudentEmbedding | None = None
    best_similarity = -1.0
    for template in active_templates:
        candidate = np.asarray(template.embedding, dtype=np.float32).flatten()
        if candidate.size == 0 or not np.isfinite(candidate).all():
            continue
        similarity = _cosine_similarity(candidate, fresh_embedding)
        if similarity > best_similarity:
            best_similarity = similarity
            best_template = template

    if best_template is None:
        return None

    template_age_days = (_as_utc(datetime.now(UTC)) - _as_utc(best_template.created_at)).days
    if template_age_days < int(settings.auto_refresh_max_age_days):
        return None

    if best_similarity < float(settings.auto_refresh_similarity_threshold):
        return None

    face_size_px = int(min(w, h))
    face_area_ratio = float((w * h) / max(int(image_bgr.shape[0] * image_bgr.shape[1]), 1))
    sharpness = float(cv2.Laplacian(crop, cv2.CV_64F).var())

    new_embedding = StudentEmbedding(
        student_id=student_id,
        pose_label=str(best_template.pose_label),
        resolution=str(best_template.resolution),
        model_name=str(best_template.model_name),
        embedding=fresh_embedding.tolist(),
        capture_quality_score=quality,
        sharpness=sharpness,
        face_size_px=face_size_px,
        face_area_ratio=face_area_ratio,
        embedding_norm=float(np.linalg.norm(fresh_embedding)),
        novelty_score=float(best_template.novelty_score or 0.0),
        collision_risk=float(best_template.collision_risk or 0.0),
        retention_score=float(best_template.retention_score or 0.0),
        template_status="active",
        is_active=True,
    )
    db.add(new_embedding)

    best_template.template_status = "backup"
    best_template.is_active = False

    db.flush()

    audit = TemplateAuditLog(
        student_id=student_id,
        old_embedding_id=best_template.id,
        new_embedding_id=new_embedding.id,
        refresh_confidence=confidence,
        refresh_quality=quality,
        refreshed_by=refreshed_by,
        action="refresh",
        details={
            "template_age_days": template_age_days,
            "similarity": float(best_similarity),
            "threshold": float(settings.auto_refresh_similarity_threshold),
        },
    )
    db.add(audit)
    db.flush()

    return {
        "audit_log_id": int(audit.id),
        "student_id": int(student_id),
        "old_embedding_id": int(best_template.id),
        "new_embedding_id": int(new_embedding.id),
        "refresh_confidence": confidence,
        "refresh_quality": quality,
        "template_age_days": int(template_age_days),
    }


async def list_template_refresh_logs_async(
    db: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(TemplateAuditLog)
        .order_by(TemplateAuditLog.refreshed_at.desc(), TemplateAuditLog.id.desc())
        .limit(max(1, min(int(limit), 500)))
    )
    rows = result.scalars().all()
    return [
        {
            "id": int(row.id),
            "student_id": int(row.student_id),
            "old_embedding_id": row.old_embedding_id,
            "new_embedding_id": row.new_embedding_id,
            "refresh_confidence": float(row.refresh_confidence),
            "refresh_quality": (float(row.refresh_quality) if row.refresh_quality is not None else None),
            "refreshed_at": row.refreshed_at.isoformat() if row.refreshed_at else None,
            "refreshed_by": row.refreshed_by,
            "action": row.action,
            "rollback_of_id": row.rollback_of_id,
            "details": row.details or {},
        }
        for row in rows
    ]


async def rollback_auto_refresh_async(
    db: AsyncSession,
    *,
    audit_log_id: int,
    actor: str,
) -> dict[str, Any]:
    """Rollback one refresh event by restoring old template as active."""
    result = await db.execute(
        select(TemplateAuditLog).where(TemplateAuditLog.id == audit_log_id)
    )
    refresh_log = result.scalar_one_or_none()
    if refresh_log is None or str(refresh_log.action) != "refresh":
        raise ValueError("Refresh audit event not found")

    existing_rollback = await db.execute(
        select(TemplateAuditLog.id).where(
            TemplateAuditLog.action == "rollback",
            TemplateAuditLog.rollback_of_id == audit_log_id,
        )
    )
    if existing_rollback.scalar_one_or_none() is not None:
        raise ValueError("Refresh event already rolled back")

    old_embedding = None
    new_embedding = None

    if refresh_log.old_embedding_id is not None:
        old_result = await db.execute(
            select(StudentEmbedding).where(StudentEmbedding.id == refresh_log.old_embedding_id)
        )
        old_embedding = old_result.scalar_one_or_none()

    if refresh_log.new_embedding_id is not None:
        new_result = await db.execute(
            select(StudentEmbedding).where(StudentEmbedding.id == refresh_log.new_embedding_id)
        )
        new_embedding = new_result.scalar_one_or_none()

    if old_embedding is not None:
        old_embedding.template_status = "active"
        old_embedding.is_active = True

    if new_embedding is not None:
        new_embedding.template_status = "backup"
        new_embedding.is_active = False

    rollback_log = TemplateAuditLog(
        student_id=refresh_log.student_id,
        old_embedding_id=refresh_log.new_embedding_id,
        new_embedding_id=refresh_log.old_embedding_id,
        refresh_confidence=float(refresh_log.refresh_confidence),
        refresh_quality=refresh_log.refresh_quality,
        refreshed_by=actor,
        action="rollback",
        rollback_of_id=refresh_log.id,
        details={"source_refresh_id": int(refresh_log.id)},
    )
    db.add(rollback_log)
    await db.flush()

    return {
        "rollback_log_id": int(rollback_log.id),
        "source_refresh_id": int(refresh_log.id),
        "student_id": int(refresh_log.student_id),
        "old_embedding_id": refresh_log.old_embedding_id,
        "new_embedding_id": refresh_log.new_embedding_id,
    }


async def template_age_distribution_async(db: AsyncSession) -> list[dict[str, Any]]:
    """Build histogram buckets for active template ages in days."""
    result = await db.execute(
        select(StudentEmbedding.created_at).where(
            StudentEmbedding.template_status == "active",
            StudentEmbedding.is_active.is_(True),
        )
    )
    now = datetime.now(UTC)
    buckets = {
        "0-30": 0,
        "31-90": 0,
        "91-180": 0,
        "181-365": 0,
        "366+": 0,
    }

    for (created_at,) in result.all():
        age_days = (now - _as_utc(created_at)).days
        if age_days <= 30:
            buckets["0-30"] += 1
        elif age_days <= 90:
            buckets["31-90"] += 1
        elif age_days <= 180:
            buckets["91-180"] += 1
        elif age_days <= 365:
            buckets["181-365"] += 1
        else:
            buckets["366+"] += 1

    return [{"bucket": key, "count": int(value)} for key, value in buckets.items()]


def next_auto_refresh_due_date(from_dt: datetime | None = None) -> str:
    """Return next conservative refresh cycle date for dashboard hints."""
    base = _as_utc(from_dt) if from_dt else datetime.now(UTC)
    due = base + timedelta(days=int(settings.auto_refresh_max_age_days))
    return due.date().isoformat()
