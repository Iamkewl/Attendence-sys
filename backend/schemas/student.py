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
    model_name: str = "arcface"


class EmbeddingRead(BaseModel):
    """Student embedding response DTO."""

    id: int
    student_id: int
    pose_label: str
    resolution: str
    model_name: str

    model_config = {"from_attributes": True}
