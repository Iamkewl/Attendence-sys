"""Course and Schedule models.

New in V2: Courses are a separate entity (normalized from V1's
schedules table). Each course has an instructor (FK→users).
"""

from sqlalchemy import JSON, ForeignKey, Integer, String, Time
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class Course(Base):
    """Academic course taught by an instructor.

    New in V2 — V1 embedded course_name directly in Schedules.
    Normalizing to a separate table allows:
    - Instructor-course binding for RBAC
    - Course-level reporting
    - Multi-schedule courses (e.g., lecture + lab)
    """

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    instructor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Relationships
    instructor: Mapped["User"] = relationship("User", back_populates="courses")
    schedules: Mapped[list["Schedule"]] = relationship(
        "Schedule", back_populates="course", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Course id={self.id} code={self.code} name={self.name}>"


class Schedule(Base):
    """Class schedule — time slot when attendance is captured.

    Upgraded from V1:
    - Links to Course instead of embedding course_name
    - Course provides instructor context for RBAC
    """

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    room_id: Mapped[int] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_time: Mapped[Time] = mapped_column(Time, nullable=False)
    end_time: Mapped[Time] = mapped_column(Time, nullable=False)
    days_of_week: Mapped[list[str]] = mapped_column(
        ARRAY(String(12)).with_variant(JSON(), "sqlite"),
        nullable=False,
    )

    # Relationships
    course: Mapped["Course"] = relationship("Course", back_populates="schedules")
    room: Mapped["Room"] = relationship("Room", back_populates="schedules")
    snapshots: Mapped[list["Snapshot"]] = relationship(
        "Snapshot", back_populates="schedule", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Schedule id={self.id} course_id={self.course_id} "
            f"room_id={self.room_id} {self.start_time}-{self.end_time}>"
        )


# Forward references
from backend.models.user import User  # noqa: E402, F811
from backend.models.room import Room  # noqa: E402, F811
from backend.models.attendance import Snapshot  # noqa: E402, F811
