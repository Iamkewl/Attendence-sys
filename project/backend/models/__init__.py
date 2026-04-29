"""SQLAlchemy models — re-export all entities for Alembic and imports."""

from backend.models.audit import AuditLog  # noqa: F401
from backend.models.attendance import Detection, Snapshot  # noqa: F401
from backend.models.course import Course, Schedule  # noqa: F401
from backend.models.governance import CameraDriftEvent, TemplateAuditLog  # noqa: F401
from backend.models.room import Device, Room  # noqa: F401
from backend.models.student import Student, StudentEmbedding  # noqa: F401
from backend.models.user import RefreshToken, User  # noqa: F401
