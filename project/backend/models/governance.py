"""Governance and compliance models for template lifecycle and drift monitoring."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class TemplateAuditLog(Base):
    """Audit table for automatic template refresh and rollback actions."""

    __tablename__ = "template_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_embedding_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_embeddings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    new_embedding_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_embeddings.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    refresh_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    refresh_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    refreshed_by: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    action: Mapped[str] = mapped_column(String(24), nullable=False, default="refresh", index=True)
    rollback_of_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    details: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    student: Mapped["Student"] = relationship("Student")


class CameraDriftEvent(Base):
    """Per-camera drift alert history for investigation workflows."""

    __tablename__ = "camera_drift_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    camera_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    current_rate: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_rate: Mapped[float] = mapped_column(Float, nullable=False)
    drop_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )


# Forward references
from backend.models.student import Student  # noqa: E402, F811
