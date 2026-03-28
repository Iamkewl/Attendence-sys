"""WebSocket routes — device connections and dashboard live feeds.

Provides two WebSocket endpoints:
1. /ws/device/{device_id} — Camera/laptop devices connect to receive CAPTURE signals
2. /ws/attendance/{schedule_id} — Dashboard clients subscribe to live attendance events
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_async_session
from backend.models.room import Device
from backend.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Attendance Dashboard Feed ──────────────────────────────────────


class AttendanceBroadcaster:
    """Manages WebSocket connections for live attendance dashboard updates.

    Dashboard clients subscribe to a schedule_id and receive real-time
    detection events as students are recognized.
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, set[WebSocket]] = {}

    async def subscribe(self, schedule_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._subscribers.setdefault(schedule_id, set()).add(websocket)
        logger.info(
            "ws_attendance: dashboard subscribed to schedule %d", schedule_id
        )

    def unsubscribe(self, schedule_id: int, websocket: WebSocket) -> None:
        subs = self._subscribers.get(schedule_id)
        if subs:
            subs.discard(websocket)
            if not subs:
                del self._subscribers[schedule_id]

    async def broadcast(self, schedule_id: int, event: dict) -> int:
        """Broadcast an attendance event to all dashboard subscribers.

        Returns the number of clients successfully notified.
        """
        subs = self._subscribers.get(schedule_id, set())
        if not subs:
            return 0

        import json

        message = json.dumps(event)
        dead: list[WebSocket] = []
        sent = 0

        for ws in subs:
            try:
                await ws.send_text(message)
                sent += 1
            except Exception:
                dead.append(ws)

        for ws in dead:
            subs.discard(ws)

        return sent

    @property
    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subscribers.values())


# Module-level singleton
attendance_broadcaster = AttendanceBroadcaster()


# ── Device WebSocket Endpoint ──────────────────────────────────────


@router.websocket("/device/{device_id}")
async def device_ws(
    websocket: WebSocket,
    device_id: int,
    room_id: int = Query(...),
    secret: str = Query(...),
):
    """WebSocket endpoint for device connections.

    Devices connect with their device_id, room_id, and secret key.
    Once authenticated, they stay connected and receive CAPTURE signals
    from the orchestrator when a heartbeat trigger fires.

    Protocol:
    - Server → Device: JSON {"action": "CAPTURE", "nonce": "...", ...}
    - Device → Server: Binary image data or JSON status updates
    """
    # TODO: Validate device secret against DB
    # For now, accept connection and register
    await ws_manager.connect(device_id, room_id, websocket)

    try:
        while True:
            # Devices can send status updates or image data
            data = await websocket.receive_text()
            logger.debug(
                "ws_device: received from device %d: %s",
                device_id,
                data[:100],
            )
    except WebSocketDisconnect:
        ws_manager.disconnect(device_id, room_id)
        logger.info(
            "ws_device: device %d disconnected from room %d",
            device_id,
            room_id,
        )


# ── Attendance Dashboard WebSocket Endpoint ────────────────────────


@router.websocket("/attendance/{schedule_id}")
async def attendance_ws(
    websocket: WebSocket,
    schedule_id: int,
):
    """WebSocket endpoint for live attendance dashboard.

    Dashboard clients connect to a schedule_id and receive real-time
    events as students are detected:

    Event types:
    - {"type": "detection", "student_id": 42, "confidence": 0.95, ...}
    - {"type": "snapshot_complete", "snapshot_id": 123, "count": 5}
    - {"type": "liveness_check", "result": "passed", ...}
    """
    await attendance_broadcaster.subscribe(schedule_id, websocket)

    try:
        while True:
            # Keep connection alive; clients can send ping/filter messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        attendance_broadcaster.unsubscribe(schedule_id, websocket)
        logger.info(
            "ws_attendance: dashboard disconnected from schedule %d",
            schedule_id,
        )
