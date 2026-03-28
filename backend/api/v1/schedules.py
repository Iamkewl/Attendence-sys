"""Schedule management routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role
from backend.core.constants import UserRole
from backend.models.course import Schedule
from backend.models.user import User
from backend.schemas.course import ScheduleCreate, ScheduleRead

router = APIRouter()


@router.post(
    "",
    response_model=ScheduleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a schedule",
)
async def create_schedule(
    body: ScheduleCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Create a class schedule for a course."""
    schedule = Schedule(**body.model_dump())
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


@router.get(
    "",
    response_model=list[ScheduleRead],
    summary="List schedules",
)
async def list_schedules(
    course_id: int | None = None,
    room_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """List schedules with optional course/room filters."""
    query = select(Schedule).offset(skip).limit(limit)
    if course_id:
        query = query.where(Schedule.course_id == course_id)
    if room_id:
        query = query.where(Schedule.room_id == room_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{schedule_id}",
    response_model=ScheduleRead,
    summary="Get schedule by ID",
)
async def get_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Get a specific schedule."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.delete(
    "/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete schedule",
)
async def delete_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Delete a schedule (admin only)."""
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await db.delete(schedule)
