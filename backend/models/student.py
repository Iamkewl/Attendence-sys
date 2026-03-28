"""Student and StudentEmbedding models.

Upgraded from V1:
- Students now link to Users table (optional, for student self-service)
- Added department and enrollment_year metadata
- StudentEmbeddings use pgvector VECTOR(512) instead of ARRAY(Float)
  for native HNSW approximate nearest-neighbor search
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.constants import EMBEDDING_DIMENSION
from backend.db.base import Base


class Student(Base):
    """Enrolled student with face embeddings for recognition.

    Each student can optionally link to a User account for
    self-service attendance viewing.
    """

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enrollment_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_enrolled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    user: Mapped["User | None"] = relationship("User", back_populates="student")
    embeddings: Mapped[list["StudentEmbedding"]] = relationship(
        "StudentEmbedding",
        back_populates="student",
        cascade="all, delete-orphan",
    )
    detections: Mapped[list["Detection"]] = relationship(
        "Detection", back_populates="student"
    )

    def __repr__(self) -> str:
        return f"<Student id={self.id} name={self.name} enrolled={self.is_enrolled}>"


class StudentEmbedding(Base):
    """Multi-pose, multi-resolution, multi-model face embedding template.

    Each student can have embeddings for:
      - pose_label: frontal | left_34 | right_34
      - resolution: full | low_res
      - model_name: arcface | adaface

    V2 uses pgvector VECTOR(512) with HNSW index for O(log n)
    approximate nearest-neighbor search, replacing V1's ARRAY(Float)
    which required O(n) NumPy cosine scans.
    """

    __tablename__ = "student_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pose_label: Mapped[str] = mapped_column(
        String(20), nullable=False, default="frontal"
    )
    resolution: Mapped[str] = mapped_column(
        String(20), nullable=False, default="full"
    )
    model_name: Mapped[str] = mapped_column(
        String(20), nullable=False, default="arcface"
    )
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSION), nullable=False
    )

    # Relationships
    student: Mapped["Student"] = relationship(
        "Student", back_populates="embeddings"
    )

    def __repr__(self) -> str:
        return (
            f"<StudentEmbedding id={self.id} student_id={self.student_id} "
            f"pose={self.pose_label} model={self.model_name}>"
        )


# Forward references
from backend.models.user import User  # noqa: E402, F811
from backend.models.attendance import Detection  # noqa: E402, F811
