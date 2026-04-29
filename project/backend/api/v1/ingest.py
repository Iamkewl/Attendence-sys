"""Ingest routes — HMAC-authenticated device snapshot/clip upload.

V2: Full HMAC-SHA256 verification + Redis nonce replay protection.
Image data is dispatched to Celery CV workers for async processing.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db
from backend.core.constants import ErrorCode
from backend.services.hmac_auth import compute_payload_digest, verify_signature, verify_device_secret
from backend.services.redis_service import check_rate_limit, store_nonce
from backend.models.course import Schedule
from backend.models.room import Device
from backend.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

router = APIRouter()


class IngestResponse(BaseModel):
    status: str
    message: str
    task_id: str | None = None
    snapshot_id: int | None = None


def _validate_timestamp(timestamp: str, skew_seconds: int = 120) -> None:
    """Reject stale or malformed ingest timestamps to reduce replay surface."""
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.HMAC_INVALID, "message": "Invalid timestamp"},
        )

    if abs(int(time.time()) - ts) > skew_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.HMAC_INVALID, "message": "Timestamp outside allowed window"},
        )


async def _verify_device_request(
    *,
    db: AsyncSession,
    device_id: int,
    schedule_id: int,
    nonce: str,
    signature: str,
    device_secret: str,
    payload_digest: str,
) -> None:
    """Verify device identity, schedule binding, HMAC signature, and nonce."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": ErrorCode.DEVICE_NOT_FOUND, "message": "Device not found"},
        )

    if not verify_device_secret(device_secret, device.secret_key_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.HMAC_INVALID, "message": "Invalid device secret"},
        )

    schedule_room = await db.execute(select(Schedule.room_id).where(Schedule.id == schedule_id))
    schedule_room_id = schedule_room.scalar_one_or_none()
    if schedule_room_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": ErrorCode.NOT_FOUND, "message": "Schedule not found"},
        )

    if schedule_room_id != device.room_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": ErrorCode.FORBIDDEN, "message": "Device is not assigned to schedule room"},
        )

    if not verify_signature(payload_digest, nonce, device_secret, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": ErrorCode.HMAC_INVALID, "message": "Invalid signature"},
        )

    nonce_ok = await store_nonce(str(device_id), nonce, ttl_seconds=60)
    if not nonce_ok:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": ErrorCode.NONCE_REPLAYED, "message": "Nonce already used"},
        )


@router.post(
    "",
    response_model=IngestResponse,
    summary="Device uploads a snapshot with HMAC-SHA256 auth",
)
async def ingest_snapshot(
    image: UploadFile = File(...),
    device_id: int = Form(...),
    schedule_id: int = Form(...),
    camera_id: str = Form(...),
    timestamp: str = Form(...),
    nonce: str = Form(...),
    signature: str = Header(..., alias="X-Signature"),
    device_secret: str = Header(..., alias="X-Device-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Receive a snapshot from a device with HMAC-SHA256 verification.

    Steps:
    1. Rate limit check (per device)
    2. Nonce replay protection (Redis SET NX)
    3. HMAC-SHA256 signature verification
    4. Dispatch to Celery CV worker for async face recognition
    """
    # 1. Rate limit
    allowed = await check_rate_limit(
        f"ingest:{device_id}", max_requests=6, window_seconds=30
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": ErrorCode.RATE_LIMITED, "message": "Too many requests"},
        )

    _validate_timestamp(timestamp)

    # 2. Read image and compute payload digest
    image_bytes = await image.read()
    payload_digest = compute_payload_digest(image_bytes, device_id, timestamp)

    # 3. Device/schedule auth + signature verification + nonce protection
    await _verify_device_request(
        db=db,
        device_id=device_id,
        schedule_id=schedule_id,
        nonce=nonce,
        signature=signature,
        device_secret=device_secret,
        payload_digest=payload_digest,
    )

    # 4. Dispatch to Celery CV worker
    import base64

    task = celery_app.send_task(
        "backend.workers.cv_tasks.process_snapshot",
        kwargs={
            "image_b64": base64.b64encode(image_bytes).decode("utf-8"),
            "device_id": device_id,
            "schedule_id": schedule_id,
            "camera_id": camera_id,
        },
    )

    return IngestResponse(
        status="dispatched",
        message="Snapshot dispatched to CV worker",
        task_id=task.id,
    )


@router.post(
    "/clip",
    response_model=IngestResponse,
    summary="Device uploads a 5s heartbeat video clip",
)
async def ingest_clip(
    clip: UploadFile = File(...),
    device_id: int = Form(...),
    schedule_id: int = Form(...),
    camera_id: str = Form(...),
    timestamp: str = Form(...),
    nonce: str = Form(...),
    signature: str = Header(..., alias="X-Signature"),
    device_secret: str = Header(..., alias="X-Device-Secret"),
    db: AsyncSession = Depends(get_db),
):
    """Receive a video clip for liveness verification + multi-frame voting.

    Dispatches to Celery CV worker for async processing.
    """
    allowed = await check_rate_limit(
        f"ingest_clip:{device_id}", max_requests=2, window_seconds=30
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": ErrorCode.RATE_LIMITED, "message": "Too many requests"},
        )

    _validate_timestamp(timestamp)

    clip_bytes = await clip.read()
    payload_digest = compute_payload_digest(clip_bytes, device_id, timestamp)

    await _verify_device_request(
        db=db,
        device_id=device_id,
        schedule_id=schedule_id,
        nonce=nonce,
        signature=signature,
        device_secret=device_secret,
        payload_digest=payload_digest,
    )

    import base64

    task = celery_app.send_task(
        "backend.workers.cv_tasks.process_clip",
        kwargs={
            "clip_b64": base64.b64encode(clip_bytes).decode("utf-8"),
            "device_id": device_id,
            "schedule_id": schedule_id,
            "camera_id": camera_id,
        },
    )

    return IngestResponse(
        status="dispatched",
        message="Clip dispatched to CV worker for liveness + recognition",
        task_id=task.id,
    )
