"""Snapshot orchestrator — ported from V1.

Triggers CAPTURE signals to all devices in rooms with active schedules.
V2: Uses async SQLAlchemy session and Redis nonce store.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.models.course import Schedule
from backend.services.camera_profiles import camera_supports_flash_liveness
from backend.services.redis_service import store_nonce
from backend.services.websocket_manager import ws_manager

settings = get_settings()


async def trigger_active_schedule_snapshots(db: AsyncSession) -> dict:
    """Check for active schedules and send CAPTURE to their room devices.

    Called by APScheduler every minute. Only triggers when the current
    minute aligns with the snapshot_interval_minutes setting.

    Returns:
        Dict with triggered signal count and active schedule count.
    """
    now = datetime.now()
    day_name = now.strftime("%A").lower()

    if now.minute % settings.snapshot_interval_minutes != 0:
        return {"triggered": 0, "reason": "not_interval"}

    # Find active schedules
    result = await db.execute(select(Schedule))
    all_schedules = result.scalars().all()

    active = []
    for schedule in all_schedules:
        days = [d.lower() for d in schedule.days_of_week]
        if (
            schedule.start_time <= now.time() <= schedule.end_time
            and day_name in days
        ):
            active.append(schedule)

    total_signals = 0
    for schedule in active:
        nonce = secrets.token_hex(16)
        # Store nonce in Redis (V2) instead of in-memory dict (V1)
        await store_nonce(
            device_id=str(schedule.room_id),
            nonce=nonce,
            ttl_seconds=settings.nonce_ttl_seconds,
        )

        payload = {
            "action": "CAPTURE",
            "nonce": nonce,
            "schedule_id": schedule.id,
            "timestamp": now.isoformat(),
            "burst_count": int(max(settings.burst_capture_count, 1)),
            "burst_gap_ms": int(max(settings.burst_capture_gap_ms, 0)),
        }
        sent = await ws_manager.send_capture(schedule.room_id, payload)
        total_signals += sent

    return {"triggered": total_signals, "active_schedules": len(active)}


async def trigger_flash_capture(
    room_id: int,
    schedule_id: int,
    *,
    camera_id: str | None = None,
) -> dict:
    """Trigger flash-off/flash-on pair capture for flash liveness checks.

    The action is capability-gated by camera profile so cameras without flash
    support continue normal attendance flow.
    """
    if camera_id and not camera_supports_flash_liveness(camera_id):
        return {"triggered": 0, "reason": "camera_flash_not_supported"}

    now = datetime.now()
    nonce = secrets.token_hex(16)
    await store_nonce(
        device_id=str(room_id),
        nonce=nonce,
        ttl_seconds=settings.nonce_ttl_seconds,
    )

    payload = {
        "action": "FLASH_CAPTURE",
        "nonce": nonce,
        "schedule_id": schedule_id,
        "timestamp": now.isoformat(),
        "camera_id": camera_id,
        "flash_sequence": ["flash_off", "flash_on"],
    }
    sent = await ws_manager.send_capture(room_id, payload)
    return {
        "triggered": sent,
        "action": "FLASH_CAPTURE",
        "camera_id": camera_id,
    }
