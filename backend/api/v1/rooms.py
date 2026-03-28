"""Room management routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role
from backend.core.constants import UserRole
from backend.models.room import Room
from backend.models.user import User

router = APIRouter()


class RoomCreate(BaseModel):
    room_name: str = Field(min_length=1, max_length=120)
    capacity: int | None = None


class RoomRead(BaseModel):
    id: int
    room_name: str
    capacity: int | None
    model_config = {"from_attributes": True}


@router.post(
    "",
    response_model=RoomRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a room",
)
async def create_room(
    body: RoomCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    room = Room(**body.model_dump())
    db.add(room)
    await db.flush()
    await db.refresh(room)
    return room


@router.get(
    "",
    response_model=list[RoomRead],
    summary="List rooms",
)
async def list_rooms(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(select(Room).offset(skip).limit(limit))
    return result.scalars().all()


@router.get(
    "/{room_id}",
    response_model=RoomRead,
    summary="Get room by ID",
)
async def get_room(
    room_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(select(Room).where(Room.id == room_id))
    room = result.scalar_one_or_none()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return room
