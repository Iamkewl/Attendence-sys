"""Redis Pub/Sub event bridge — connects CV workers to real-time clients.

Flow:
1. Celery CV worker processes a snapshot and publishes detection events to Redis
2. This bridge subscribes to Redis channels and dispatches events to:
   - SSE broadcaster (attendance_sse_stream subscribers)
   - WebSocket attendance broadcaster (dashboard WebSocket subscribers)

Runs as a background asyncio task within the FastAPI process.
"""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

# Redis channel names
CHANNEL_DETECTIONS = "attendance:detections"
CHANNEL_SNAPSHOTS = "attendance:snapshots"


async def publish_detection_event(
    schedule_id: int,
    student_id: int,
    confidence: float,
    snapshot_id: int,
    camera_id: str,
) -> None:
    """Publish a detection event to Redis (called from CV workers).

    Since Celery workers are sync, this is called via a sync Redis client
    in cv_tasks.py.
    """
    import redis as sync_redis
    from backend.core.config import get_settings

    settings = get_settings()
    r = sync_redis.Redis.from_url(settings.redis_url)
    event = {
        "type": "detection",
        "schedule_id": schedule_id,
        "student_id": student_id,
        "confidence": round(confidence, 4),
        "snapshot_id": snapshot_id,
        "camera_id": camera_id,
    }
    r.publish(CHANNEL_DETECTIONS, json.dumps(event))
    r.close()


async def publish_snapshot_complete(
    schedule_id: int,
    snapshot_id: int,
    total_detected: int,
    new_detections: int,
) -> None:
    """Publish a snapshot completion event to Redis."""
    import redis as sync_redis
    from backend.core.config import get_settings

    settings = get_settings()
    r = sync_redis.Redis.from_url(settings.redis_url)
    event = {
        "type": "snapshot_complete",
        "schedule_id": schedule_id,
        "snapshot_id": snapshot_id,
        "total_detected": total_detected,
        "new_detections": new_detections,
    }
    r.publish(CHANNEL_SNAPSHOTS, json.dumps(event))
    r.close()


async def start_event_bridge() -> asyncio.Task:
    """Start the Redis → SSE/WebSocket event bridge as a background task.

    Called from the FastAPI lifespan on startup.
    Returns the asyncio.Task handle for cleanup on shutdown.
    """

    async def _bridge_loop():
        try:
            import redis.asyncio as aioredis
            from backend.core.config import get_settings

            settings = get_settings()
            r = aioredis.from_url(settings.redis_url)
            pubsub = r.pubsub()
            await pubsub.subscribe(CHANNEL_DETECTIONS, CHANNEL_SNAPSHOTS)

            logger.info("event_bridge: listening on Redis channels")

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    event = json.loads(message["data"])
                    schedule_id = event.get("schedule_id")

                    if schedule_id is None:
                        continue

                    # Dispatch to SSE broadcaster
                    try:
                        from backend.api.v1.sse import sse_broadcaster
                        await sse_broadcaster.publish(schedule_id, event)
                    except Exception:
                        pass

                    # Dispatch to WebSocket attendance broadcaster
                    try:
                        from backend.api.v1.websocket import (
                            attendance_broadcaster,
                        )
                        await attendance_broadcaster.broadcast(
                            schedule_id, event
                        )
                    except Exception:
                        pass

                except json.JSONDecodeError:
                    logger.warning(
                        "event_bridge: invalid JSON from Redis"
                    )

        except asyncio.CancelledError:
            logger.info("event_bridge: shutting down")
        except Exception as exc:
            logger.error("event_bridge: fatal error: %s", exc, exc_info=True)

    task = asyncio.create_task(_bridge_loop())
    return task
