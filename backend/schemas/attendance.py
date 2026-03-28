"""Attendance and reporting schemas."""

from datetime import datetime

from pydantic import BaseModel


class DetectionEvent(BaseModel):
    """Single detection event (used in SSE streams)."""

    student_id: int
    student_name: str
    confidence: float
    camera_id: str
    timestamp: datetime


class AttendanceRecord(BaseModel):
    """Per-student attendance summary for a schedule."""

    student_id: int
    student_name: str
    observed_snapshots: int
    total_snapshots: int
    ratio: float
    status: str  # "present" or "absent"


class AttendanceReport(BaseModel):
    """Attendance report for a schedule/date range."""

    schedule_id: int
    course_name: str
    total_snapshots: int
    records: list[AttendanceRecord]


class SnapshotRead(BaseModel):
    """Snapshot response DTO."""

    id: int
    schedule_id: int
    timestamp: datetime
    expected_count: int

    model_config = {"from_attributes": True}


class DetectionRead(BaseModel):
    """Detection response DTO."""

    id: int
    snapshot_id: int
    student_id: int
    confidence: float
    camera_id: str

    model_config = {"from_attributes": True}
