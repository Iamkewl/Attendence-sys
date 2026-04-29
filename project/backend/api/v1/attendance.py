"""Attendance query and reporting routes."""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role, get_current_user
from backend.core.constants import ATTENDANCE_PRESENT_THRESHOLD, UserRole
from backend.models.attendance import Detection, Snapshot
from backend.models.course import Course, Schedule
from backend.models.student import Student
from backend.models.user import User
from backend.schemas.attendance import (
    AttendanceRecord,
    AttendanceReport,
    DetectionRead,
    SnapshotRead,
)

router = APIRouter()


async def _build_attendance_report(db: AsyncSession, schedule_id: int) -> AttendanceReport:
    """Build attendance report for a schedule."""
    schedule_result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = schedule_result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    course_name = ""
    course_result = await db.execute(select(Course).where(Course.id == schedule.course_id))
    course = course_result.scalar_one_or_none()
    if course:
        course_name = f"{course.code} {course.name}".strip()

    total_result = await db.execute(
        select(func.count(Snapshot.id)).where(Snapshot.schedule_id == schedule_id)
    )
    total_snapshots = total_result.scalar() or 0

    if total_snapshots == 0:
        return AttendanceReport(
            schedule_id=schedule_id,
            course_name=course_name,
            total_snapshots=0,
            records=[],
        )

    detection_counts = await db.execute(
        select(
            Detection.student_id,
            Student.name,
            func.count(func.distinct(Detection.snapshot_id)).label("observed"),
        )
        .join(Student, Detection.student_id == Student.id)
        .join(Snapshot, Detection.snapshot_id == Snapshot.id)
        .where(Snapshot.schedule_id == schedule_id)
        .group_by(Detection.student_id, Student.name)
    )

    records = []
    for row in detection_counts:
        ratio = row.observed / total_snapshots
        records.append(
            AttendanceRecord(
                student_id=row.student_id,
                student_name=row.name,
                observed_snapshots=row.observed,
                total_snapshots=total_snapshots,
                ratio=round(ratio, 4),
                status="present" if ratio >= ATTENDANCE_PRESENT_THRESHOLD else "absent",
            )
        )

    return AttendanceReport(
        schedule_id=schedule_id,
        course_name=course_name,
        total_snapshots=total_snapshots,
        records=records,
    )


@router.get(
    "/{schedule_id}",
    response_model=AttendanceReport,
    summary="Get attendance for a schedule",
)
async def get_attendance(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Compute and return attendance report for a specific schedule.

    Uses batch aggregate query (no N+1) — ratio = observed/total snapshots.
    """
    return await _build_attendance_report(db, schedule_id)


@router.get(
    "/{schedule_id}/export",
    summary="Export attendance report as CSV",
)
async def export_attendance_csv(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Export attendance report for a schedule as CSV."""
    report = await _build_attendance_report(db, schedule_id)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["schedule_id", "course_name", "total_snapshots"])
    writer.writerow([report.schedule_id, report.course_name, report.total_snapshots])
    writer.writerow([])
    writer.writerow([
        "student_id",
        "student_name",
        "observed_snapshots",
        "total_snapshots",
        "ratio",
        "status",
    ])
    for record in report.records:
        writer.writerow([
            record.student_id,
            record.student_name,
            record.observed_snapshots,
            record.total_snapshots,
            record.ratio,
            record.status,
        ])

    csv_data = output.getvalue()
    output.close()

    filename = f"attendance_schedule_{schedule_id}.csv"
    return StreamingResponse(
        iter([csv_data]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/student/{student_id}",
    response_model=list[AttendanceRecord],
    summary="Get attendance for a student",
)
async def get_student_attendance(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get attendance records for a specific student.

    Students can only view their own records.
    """
    # RBAC: students view own only
    if current_user.role == UserRole.STUDENT:
        result = await db.execute(
            select(Student).where(Student.id == student_id, Student.user_id == current_user.id)
        )
        if not result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Access denied")

    # Get all schedules where student has detections
    detection_counts = await db.execute(
        select(
            Snapshot.schedule_id,
            func.count(func.distinct(Snapshot.id)).label("observed"),
        )
        .join(Detection, Detection.snapshot_id == Snapshot.id)
        .where(Detection.student_id == student_id)
        .group_by(Snapshot.schedule_id)
    )

    records = []
    for row in detection_counts:
        # Get total snapshots for this schedule
        total_result = await db.execute(
            select(func.count(Snapshot.id)).where(Snapshot.schedule_id == row.schedule_id)
        )
        total = total_result.scalar() or 0
        ratio = row.observed / total if total > 0 else 0

        records.append(
            AttendanceRecord(
                student_id=student_id,
                student_name="",
                observed_snapshots=row.observed,
                total_snapshots=total,
                ratio=round(ratio, 4),
                status="present" if ratio >= ATTENDANCE_PRESENT_THRESHOLD else "absent",
            )
        )

    return records
