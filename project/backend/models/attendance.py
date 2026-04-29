"""Snapshot and Detection models — attendance data.

These models are structurally identical to V1. The unique constraint
on (snapshot_id, student_id, camera_id) prevents duplicate detections.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class Snapshot(Base):
    """A single attendance capture event at a point in time.

    Created by the orchestrator (APScheduler) at each heartbeat interval.
    Devices upload frames for this snapshot, which are processed into
    detections.
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # Relationships
    schedule: Mapped["Schedule"] = relationship(
        "Schedule", back_populates="snapshots"
    )
    detections: Mapped[list["Detection"]] = relationship(
        "Detection", back_populates="snapshot", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Snapshot id={self.id} schedule_id={self.schedule_id} ts={self.timestamp}>"


class Detection(Base):
    """A single face detection event within a snapshot.

    Records that a specific student was recognized by a specific camera
    with a given confidence score. The unique constraint prevents
    the same student from being counted twice per snapshot per camera.
    """

    __tablename__ = "detections"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "student_id", "camera_id",
            name="uq_detection_triplet",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"), nullable=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    camera_id: Mapped[str] = mapped_column(String(100), nullable=False)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    cross_camera_source_track_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    # Relationships
    snapshot: Mapped["Snapshot"] = relationship(
        "Snapshot", back_populates="detections"
    )
    student: Mapped["Student"] = relationship(
        "Student", back_populates="detections"
    )

    def __repr__(self) -> str:
        return (
            f"<Detection id={self.id} snapshot_id={self.snapshot_id} "
            f"student_id={self.student_id} conf={self.confidence:.3f}>"
        )


# Forward references
from backend.models.course import Schedule  # noqa: E402, F811
from backend.models.student import Student  # noqa: E402, F811
