"""Audit log model — new in V2.

Every state-changing API call is logged with user attribution,
action type, resource, and JSONB details for queryable audit trail.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base


class AuditLog(Base):
    """Immutable audit trail for all state-changing operations.

    Logged by the audit_service middleware on every POST/PUT/PATCH/DELETE.

    Attributes:
        action: The operation performed (e.g., 'user.create', 'student.enroll')
        resource: The resource type affected (e.g., 'users', 'students')
        details: JSONB payload with operation-specific context (e.g., changed fields)
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Relationships
    user: Mapped["User | None"] = relationship("User", back_populates="audit_logs")

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} user_id={self.user_id} "
            f"action={self.action} resource={self.resource}>"
        )


# Forward reference
from backend.models.user import User  # noqa: E402, F811
