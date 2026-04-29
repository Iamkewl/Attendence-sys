"""Course and Schedule request/response schemas."""

from datetime import time

from pydantic import BaseModel, Field


class CourseCreate(BaseModel):
    """Create a new course."""

    code: str = Field(min_length=2, max_length=20)
    name: str = Field(min_length=1, max_length=255)
    instructor_id: int
    department: str | None = None


class CourseRead(BaseModel):
    """Course response DTO."""

    id: int
    code: str
    name: str
    instructor_id: int
    department: str | None

    model_config = {"from_attributes": True}


class CourseUpdate(BaseModel):
    """Course update."""

    code: str | None = Field(default=None, min_length=2, max_length=20)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    instructor_id: int | None = None
    department: str | None = None


class ScheduleCreate(BaseModel):
    """Create a class schedule."""

    course_id: int
    room_id: int
    start_time: time
    end_time: time
    days_of_week: list[str] = Field(
        min_length=1,
        max_length=7,
        examples=[["Monday", "Wednesday", "Friday"]],
    )


class ScheduleRead(BaseModel):
    """Schedule response DTO."""

    id: int
    course_id: int
    room_id: int
    start_time: time
    end_time: time
    days_of_week: list[str]

    model_config = {"from_attributes": True}
