"""APScheduler heartbeat trigger — periodic snapshot orchestration.

Runs as a background task within the FastAPI process (not in Celery).
Every N minutes, it checks for active schedules and sends CAPTURE signals
to all IoT devices in the corresponding rooms.

V2: Uses APScheduler 3.x AsyncIOScheduler for non-blocking execution.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.core.config import get_settings
from backend.db.session import async_session_factory

logger = logging.getLogger(__name__)
settings = get_settings()

# Module-level scheduler instance
scheduler = AsyncIOScheduler()


async def _heartbeat_job() -> None:
    """Periodic heartbeat — trigger snapshot captures for active schedules.

    This job runs every `snapshot_interval_minutes` and:
    1. Queries for active schedules (matching current day/time)
    2. Generates nonces (stored in Redis)
    3. Sends CAPTURE signals to devices via WebSocket
    """
    try:
        from backend.services.orchestrator import (
            trigger_active_schedule_snapshots,
        )

        async with async_session_factory() as session:
            result = await trigger_active_schedule_snapshots(session)

        if result.get("triggered", 0) > 0:
            logger.info(
                "heartbeat: triggered %d capture signals for %d active schedules",
                result["triggered"],
                result["active_schedules"],
            )
        else:
            reason = result.get("reason", "no_active_schedules")
            logger.debug("heartbeat: no captures triggered (%s)", reason)
    except Exception as exc:
        logger.error("heartbeat: job failed: %s", exc, exc_info=True)


async def _cleanup_job() -> None:
    """Periodic cleanup of expired sessions, stale connections, etc."""
    try:
        from backend.services.websocket_manager import ws_manager

        logger.debug(
            "cleanup: %d devices connected", ws_manager.connected_count
        )
    except Exception as exc:
        logger.error("cleanup: job failed: %s", exc, exc_info=True)


def start_scheduler() -> None:
    """Start the APScheduler with heartbeat and cleanup jobs.

    Called from the FastAPI lifespan on startup.
    """
    # Heartbeat: trigger captures every N minutes
    scheduler.add_job(
        _heartbeat_job,
        trigger=IntervalTrigger(minutes=settings.snapshot_interval_minutes),
        id="heartbeat_trigger",
        name="Snapshot Capture Heartbeat",
        replace_existing=True,
        max_instances=1,
    )

    # Cleanup: run every 5 minutes
    scheduler.add_job(
        _cleanup_job,
        trigger=IntervalTrigger(minutes=5),
        id="cleanup_job",
        name="Connection Cleanup",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "scheduler: started with heartbeat interval=%d min",
        settings.snapshot_interval_minutes,
    )


def stop_scheduler() -> None:
    """Gracefully stop the scheduler. Called from FastAPI lifespan on shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler: stopped")
