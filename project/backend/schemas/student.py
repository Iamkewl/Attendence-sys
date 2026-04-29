"""Student request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class StudentCreate(BaseModel):
    """Create a new student record."""

    name: str = Field(min_length=1, max_length=255)
    department: str | None = None
    enrollment_year: int | None = None
    user_id: int | None = None


class StudentRead(BaseModel):
    """Student response DTO."""

    id: int
    name: str
    department: str | None
    enrollment_year: int | None
    is_enrolled: bool
    created_at: datetime
    course_ids: list[int] = Field(default_factory=list)
    course_names: list[str] = Field(default_factory=list)
    course_count: int = 0

    model_config = {"from_attributes": True}


class StudentUpdate(BaseModel):
    """Student update."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = None
    enrollment_year: int | None = None


class EnrollFromImagesRequest(BaseModel):
    """Enrollment request metadata (images sent as multipart)."""

    student_id: int


class EnrollFromEmbeddingRequest(BaseModel):
    """Enrollment via raw embedding vector."""

    student_id: int
    embedding: list[float] = Field(min_length=512, max_length=512)
    pose_label: str = "frontal"
    resolution: str = "full"
    model_name: str = "lvface"


class EmbeddingRead(BaseModel):
    """Student embedding response DTO."""

    id: int
    student_id: int
    pose_label: str
    resolution: str
    model_name: str

    model_config = {"from_attributes": True}


class EnrollmentImageQualityCheck(BaseModel):
    """Quality report for one enrollment image."""

    filename: str
    accepted: bool
    reason: str | None = None
    reject_reason_code: str | None = None
    detected_faces: int = 0
    face_size_px: int | None = None
    area_ratio: float | None = None
    sharpness: float | None = None
    quality_score: float | None = None
    embedding_norm: float | None = None
    novelty_score: float | None = None
    collision_risk: float | None = None
    retention_score: float | None = None
    template_status: str | None = None
    estimated_pose_label: str | None = None
    pose_confidence: float | None = None
    pose_label_used: str | None = None
    pose_warning: str | None = None


class EnrollmentSummaryRead(BaseModel):
    """Enrollment summary response for image-based enrollment."""

    student_id: int
    required_embeddings: int
    total_embeddings: int
    new_embeddings: int
    enrolled: bool
    pose_coverage: dict[str, int] = Field(default_factory=dict)
    missing_pose_coverage: dict[str, int] = Field(default_factory=dict)
    checks: list[EnrollmentImageQualityCheck]
    reject_reason_groups: dict[str, int] = Field(default_factory=dict)
    dominant_reject_reason_code: str | None = None
    dominant_reject_reason_label: str | None = None
    capture_guidance: list[str] = Field(default_factory=list)
    message: str


class EnrollmentTestCandidateRead(BaseModel):
    """One ranked identity candidate for enrollment verification."""

    student_id: int
    student_name: str
    score: float


class EnrollmentTestRead(BaseModel):
    """Result payload for post-enrollment verification test."""

    student_id: int
    is_match: bool
    reason: str
    detected_faces: int
    face_size_px: int | None = None
    quality_score: float | None = None
    sharpness: float | None = None
    estimated_pose_label: str | None = None
    pose_confidence: float | None = None
    face_selection_warning: str | None = None
    expected_student_score: float | None = None
    best_match_student_id: int | None = None
    best_match_student_name: str | None = None
    best_match_score: float | None = None
    second_best_score: float | None = None
    margin: float | None = None
    strict_threshold: float
    relaxed_threshold: float
    required_margin: float
    candidates: list[EnrollmentTestCandidateRead] = Field(default_factory=list)


class EnrollmentTemplateBucketRead(BaseModel):
    """Template quality summary for one pose/model/resolution bucket."""

    pose_label: str
    resolution: str
    model_name: str
    active_count: int
    backup_count: int
    quarantined_count: int
    average_quality_score: float | None = None
    average_retention_score: float | None = None


class EnrollmentQualitySummaryRead(BaseModel):
    """Quality and template distribution summary for one student."""

    student_id: int
    enrolled: bool
    required_embeddings: int
    required_pose_coverage: dict[str, int]
    active_embeddings: int
    total_embeddings: int
    pose_coverage: dict[str, int]
    missing_pose_coverage: dict[str, int]
    buckets: list[EnrollmentTemplateBucketRead]


class EnrollmentTemplateRead(BaseModel):
    """Single enrollment template with quality and lifecycle metadata."""

    id: int
    student_id: int
    pose_label: str
    resolution: str
    model_name: str
    template_status: str
    is_active: bool
    capture_quality_score: float | None = None
    sharpness: float | None = None
    retention_score: float | None = None
    novelty_score: float | None = None
    collision_risk: float | None = None

    model_config = {"from_attributes": True}


class EnrollmentTemplateStatusUpdate(BaseModel):
    """Admin action to update template lifecycle status."""

    template_status: str = Field(pattern="^(active|backup|quarantined)$")


class EnrollmentAnalyticsRead(BaseModel):
    """Enrollment analytics summary for quality and risk monitoring."""

    student_id: int
    total_templates: int
    active_templates: int
    backup_templates: int
    quarantined_templates: int
    high_collision_templates: int
    low_quality_templates: int
    average_quality_score: float | None = None
    average_retention_score: float | None = None
    quality_by_pose: dict[str, float] = Field(default_factory=dict)


class EnrollmentAnalyticsHistoryEventRead(BaseModel):
    """Historical enrollment event for timeline views."""

    timestamp: str
    event_type: str
    accepted: int | None = None
    uploaded: int | None = None
    active_embeddings: int | None = None
    total_embeddings: int | None = None
    pose_coverage: dict[str, int] = Field(default_factory=dict)
    missing_pose_coverage: dict[str, int] = Field(default_factory=dict)


class EnrollmentPoseDriftPointRead(BaseModel):
    """Per-pose active template counts over time."""

    timestamp: str
    frontal: int = 0
    left_34: int = 0
    right_34: int = 0


class EnrollmentAnalyticsHistoryRead(BaseModel):
    """Historical enrollment analytics bundle for one student."""

    student_id: int
    events: list[EnrollmentAnalyticsHistoryEventRead]
    pose_drift_timeline: list[EnrollmentPoseDriftPointRead]
