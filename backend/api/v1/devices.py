"""Device registration and management routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import bcrypt

from backend.api.deps import get_db, require_role
from backend.core.constants import UserRole
from backend.models.room import Device
from backend.models.user import User

router = APIRouter()


class DeviceCreate(BaseModel):
    room_id: int
    secret_key: str = Field(min_length=8, description="Plaintext secret, will be bcrypt-hashed")
    type: str = Field(default="camera", pattern="^(camera|laptop)$")
    rtsp_url: str | None = None


class DeviceRead(BaseModel):
    id: int
    room_id: int
    type: str
    rtsp_url: str | None
    ws_session_id: str | None
    model_config = {"from_attributes": True}


class DeviceRegistered(BaseModel):
    id: int
    room_id: int
    type: str
    message: str = "Device registered. Store the secret_key securely — it cannot be retrieved."


@router.post(
    "",
    response_model=DeviceRegistered,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new device",
)
async def register_device(
    body: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Register a new IoT device. The secret_key is bcrypt-hashed before storage."""
    hashed = bcrypt.hashpw(body.secret_key.encode(), bcrypt.gensalt()).decode()
    device = Device(
        room_id=body.room_id,
        secret_key_hash=hashed,
        type=body.type,
        rtsp_url=body.rtsp_url,
    )
    db.add(device)
    await db.flush()
    await db.refresh(device)
    return DeviceRegistered(id=device.id, room_id=device.room_id, type=device.type)


@router.get(
    "",
    response_model=list[DeviceRead],
    summary="List devices",
)
async def list_devices(
    room_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    query = select(Device).offset(skip).limit(limit)
    if room_id:
        query = query.where(Device.room_id == room_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete device",
)
async def delete_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
