"""Student management and enrollment routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role, get_current_user
from backend.core.constants import UserRole
from backend.models.student import Student, StudentEmbedding
from backend.models.user import User
from backend.schemas.student import (
    EnrollFromEmbeddingRequest,
    StudentCreate,
    StudentRead,
    StudentUpdate,
    EmbeddingRead,
)

router = APIRouter()


@router.post(
    "",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student record",
)
async def create_student(
    body: StudentCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Create a new student record."""
    student = Student(**body.model_dump())
    db.add(student)
    await db.flush()
    await db.refresh(student)
    return student


@router.get(
    "",
    response_model=list[StudentRead],
    summary="List students",
)
async def list_students(
    skip: int = 0,
    limit: int = 50,
    enrolled_only: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """List all students with optional enrollment filter."""
    query = select(Student).offset(skip).limit(limit)
    if enrolled_only:
        query = query.where(Student.is_enrolled == True)  # noqa: E712
    result = await db.execute(query)
    return result.scalars().all()


@router.get(
    "/{student_id}",
    response_model=StudentRead,
    summary="Get student by ID",
)
async def get_student(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a student by ID. Students can view their own record."""
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # RBAC: students can only view their own record
    if current_user.role == UserRole.STUDENT and student.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return student


@router.patch(
    "/{student_id}",
    response_model=StudentRead,
    summary="Update student",
)
async def update_student(
    student_id: int,
    body: StudentUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Update a student's information."""
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(student, field, value)

    await db.flush()
    await db.refresh(student)
    return student


@router.post(
    "/enroll",
    response_model=EmbeddingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll via raw embedding",
)
async def enroll_from_embedding(
    body: EnrollFromEmbeddingRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Enroll a student by directly providing a 512-d embedding vector."""
    # Verify student exists
    result = await db.execute(select(Student).where(Student.id == body.student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    embedding = StudentEmbedding(
        student_id=body.student_id,
        pose_label=body.pose_label,
        resolution=body.resolution,
        model_name=body.model_name,
        embedding=body.embedding,
    )
    db.add(embedding)

    # Mark as enrolled
    student.is_enrolled = True

    await db.flush()
    await db.refresh(embedding)
    return embedding
