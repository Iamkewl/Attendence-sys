"""Server-Sent Events (SSE) endpoint for attendance streaming.

Clients subscribe to /api/v1/sse/attendance/{schedule_id} and receive
real-time attendance detection events as they happen.

SSE is simpler than WebSocket for one-way server→client data push,
and works through proxies/load balancers without special configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user_from_token, get_db
from backend.core.constants import ErrorCode, UserRole

logger = logging.getLogger(__name__)

router = APIRouter()


class SSEBroadcaster:
    """Manages SSE subscriber queues for live attendance events.

    Each subscriber gets their own asyncio.Queue. When an event is
    published, it's pushed to all subscriber queues for that schedule.
    """

    def __init__(self) -> None:
        self._queues: dict[int, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, schedule_id: int) -> asyncio.Queue:
        """Create a new subscriber queue for a schedule."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues[schedule_id].append(queue)
        logger.info(
            "sse: new subscriber for schedule %d (total: %d)",
            schedule_id,
            len(self._queues[schedule_id]),
        )
        return queue

    def unsubscribe(self, schedule_id: int, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue."""
        queues = self._queues.get(schedule_id)
        if queues:
            try:
                queues.remove(queue)
            except ValueError:
                logger.debug("sse: double unsubscribe for schedule %d", schedule_id)
            if not queues:
                del self._queues[schedule_id]

    async def publish(self, schedule_id: int, event: dict) -> int:
        """Push an event to all subscribers for a schedule.

        Returns the number of subscribers that received the event.
        """
        queues = self._queues.get(schedule_id, [])
        sent = 0
        for queue in queues:
            try:
                queue.put_nowait(event)
                sent += 1
            except asyncio.QueueFull:
                logger.warning(
                    "sse: subscriber queue full for schedule %d, dropping event",
                    schedule_id,
                )
        return sent

    @property
    def total_subscribers(self) -> int:
        return sum(len(q) for q in self._queues.values())


class SystemSSEBroadcaster:
    """Broadcast system-wide operational alerts (drift/retention/refresh)."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._queues.append(queue)
        logger.info("sse: new system subscriber (total: %d)", len(self._queues))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        try:
            self._queues.remove(queue)
        except ValueError:
            logger.debug("sse: double unsubscribe for system subscriber")

    async def publish(self, event: dict) -> int:
        sent = 0
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
                sent += 1
            except asyncio.QueueFull:
                logger.warning("sse: system subscriber queue full, dropping event")
        return sent

    @property
    def total_subscribers(self) -> int:
        return len(self._queues)


# Module-level singleton
sse_broadcaster = SSEBroadcaster()
system_sse_broadcaster = SystemSSEBroadcaster()


@router.get(
    "/attendance/{schedule_id}",
    summary="SSE stream of live attendance events",
)
async def attendance_sse_stream(
    schedule_id: int,
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Subscribe to real-time attendance events for a schedule.

    Returns a Server-Sent Events stream. Events include:

    - `detection`: A student was recognized
    - `snapshot_complete`: All faces in a snapshot were processed
    - `heartbeat`: Keep-alive ping (every 15s)

    Example event:
    ```
    event: detection
    data: {"student_id": 42, "confidence": 0.95, "timestamp": "..."}
    ```
    """
    current_user = await get_current_user_from_token(token, db)
    if current_user.role not in {UserRole.ADMIN.value, UserRole.INSTRUCTOR.value}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ErrorCode.INSUFFICIENT_PERMISSIONS,
                "message": "Requires role: admin, instructor",
            },
        )

    queue = sse_broadcaster.subscribe(schedule_id)

    async def event_generator():
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    # Wait for event with timeout (heartbeat interval)
                    event = await asyncio.wait_for(
                        queue.get(), timeout=15.0
                    )
                    event_type = event.get("type", "message")
                    data = json.dumps(event)
                    yield f"event: {event_type}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f"event: heartbeat\ndata: {json.dumps({'type': 'heartbeat'})}\n\n"
        finally:
            sse_broadcaster.unsubscribe(schedule_id, queue)
            logger.info(
                "sse: subscriber disconnected from schedule %d", schedule_id
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get(
    "/status",
    summary="SSE broadcaster status",
)
async def sse_status():
    """Return current SSE subscriber count."""
    return {
        "total_subscribers": sse_broadcaster.total_subscribers,
        "schedules": {
            str(k): len(v)
            for k, v in sse_broadcaster._queues.items()
        },
        "system_subscribers": system_sse_broadcaster.total_subscribers,
    }


@router.get(
    "/system",
    summary="SSE stream for system operational alerts",
)
async def system_alerts_sse_stream(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Subscribe to governance/system alerts published through Redis bridge."""
    current_user = await get_current_user_from_token(token, db)
    if current_user.role not in {UserRole.ADMIN.value, UserRole.INSTRUCTOR.value}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": ErrorCode.INSUFFICIENT_PERMISSIONS,
                "message": "Requires role: admin, instructor",
            },
        )

    queue = system_sse_broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    event_type = event.get("type", "system")
                    data = json.dumps(event)
                    yield f"event: {event_type}\\ndata: {data}\\n\\n"
                except asyncio.TimeoutError:
                    yield f"event: heartbeat\\ndata: {json.dumps({'type': 'heartbeat'})}\\n\\n"
        finally:
            system_sse_broadcaster.unsubscribe(queue)
            logger.info("sse: system subscriber disconnected")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
