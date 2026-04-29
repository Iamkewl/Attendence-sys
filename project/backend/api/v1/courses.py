"""Course management routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role, get_current_user
from backend.core.constants import UserRole
from backend.models.course import Course
from backend.models.user import User
from backend.schemas.course import CourseCreate, CourseRead, CourseUpdate

router = APIRouter()


@router.post(
    "",
    response_model=CourseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a course",
)
async def create_course(
    body: CourseCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Create a new course (admin only)."""
    course = Course(**body.model_dump())
    db.add(course)
    await db.flush()
    await db.refresh(course)
    return course


@router.get(
    "",
    response_model=list[CourseRead],
    summary="List courses",
)
async def list_courses(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """List courses. Instructors see only their own courses."""
    query = select(Course).offset(skip).limit(limit)
    if current_user.role == UserRole.INSTRUCTOR:
        query = query.where(Course.instructor_id == current_user.id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{course_id}",
    response_model=CourseRead,
    summary="Get course by ID",
)
async def get_course(
    course_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Get a specific course by ID."""
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@router.patch(
    "/{course_id}",
    response_model=CourseRead,
    summary="Update course",
)
async def update_course(
    course_id: int,
    body: CourseUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Update a course (admin only)."""
    result = await db.execute(select(Course).where(Course.id == course_id))
    course = result.scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(course, field, value)

    await db.flush()
    await db.refresh(course)
    return course
