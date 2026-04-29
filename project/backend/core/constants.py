"""Application-wide constants, enums, and error codes."""

from enum import StrEnum


class UserRole(StrEnum):
    """User roles for RBAC enforcement."""

    ADMIN = "admin"
    INSTRUCTOR = "instructor"
    STUDENT = "student"
    DEVICE = "device"


class PoseLabel(StrEnum):
    """Supported pose labels for multi-pose enrollment."""

    FRONTAL = "frontal"
    LEFT_34 = "left_34"
    RIGHT_34 = "right_34"


class EmbeddingResolution(StrEnum):
    """Embedding resolution tiers."""

    FULL = "full"
    LOW_RES = "low_res"


class EmbeddingModel(StrEnum):
    """Supported face embedding models."""

    ARCFACE = "arcface"
    ADAFACE = "adaface"
    LVFACE = "lvface"


class DeviceType(StrEnum):
    """Device hardware types."""

    CAMERA = "camera"
    LAPTOP = "laptop"


# ── Error Codes ───────────────────────────────────────────

class ErrorCode(StrEnum):
    """Standardized API error codes."""

    # Auth
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    TOKEN_REVOKED = "TOKEN_REVOKED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"

    # RBAC
    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"
    FORBIDDEN = "FORBIDDEN"

    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"

    # Device / Ingest
    HMAC_INVALID = "HMAC_INVALID"
    NONCE_REPLAYED = "NONCE_REPLAYED"
    RATE_LIMITED = "RATE_LIMITED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"

    # AI Pipeline
    ENROLLMENT_FAILED = "ENROLLMENT_FAILED"
    INSUFFICIENT_FACES = "INSUFFICIENT_FACES"
    MODEL_NOT_READY = "MODEL_NOT_READY"

    # General
    INTERNAL_ERROR = "INTERNAL_ERROR"


# ── Defaults ──────────────────────────────────────────────

ATTENDANCE_PRESENT_THRESHOLD = 0.85
MIN_ENROLLMENT_PHOTOS = 5
EMBEDDING_DIMENSION = 512
NONCE_DEDUP_WINDOW_MINUTES = 5
