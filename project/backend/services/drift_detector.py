"""Camera-domain drift detection and alert publishing."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.models.attendance import Detection, Snapshot
from backend.models.governance import CameraDriftEvent

settings = get_settings()


def _as_utc(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.now(UTC)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _camera_daily_rates(rows: list[tuple[str, int, datetime]]) -> dict[str, list[dict[str, Any]]]:
    snapshot_sets: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    detection_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for camera_id, snapshot_id, timestamp in rows:
        key = str(camera_id).strip()
        if not key:
            continue
        day_key = _as_utc(timestamp).date().isoformat()
        snapshot_sets[key][day_key].add(int(snapshot_id))
        detection_counts[key][day_key] += 1

    payload: dict[str, list[dict[str, Any]]] = {}
    for camera_id, day_snapshots in snapshot_sets.items():
        points: list[dict[str, Any]] = []
        for day_key in sorted(day_snapshots.keys()):
            snapshot_count = len(day_snapshots[day_key])
            detection_count = int(detection_counts[camera_id].get(day_key, 0))
            rate = float(detection_count / max(snapshot_count, 1))
            points.append(
                {
                    "day": day_key,
                    "snapshot_count": snapshot_count,
                    "detection_count": detection_count,
                    "recognition_rate": round(rate, 6),
                }
            )
        payload[camera_id] = points
    return payload


def evaluate_camera_drift_sync(db: Session) -> dict[str, Any]:
    """Evaluate rolling 7-day camera recognition rates and emit drift alerts."""
    if not settings.enable_camera_drift_detection:
        return {"status": "skipped", "reason": "enable_camera_drift_detection=false"}

    now = datetime.now(UTC)
    window_days = int(max(settings.drift_window_days, 2))
    threshold = float(max(settings.drift_drop_threshold, 0.01))
    baseline_min_days = int(max(settings.drift_min_baseline_days, 1))
    window_start = now - timedelta(days=window_days)

    rows = db.execute(
        select(Detection.camera_id, Detection.snapshot_id, Snapshot.timestamp)
        .join(Snapshot, Snapshot.id == Detection.snapshot_id)
        .where(Snapshot.timestamp >= window_start)
    ).all()
    series = _camera_daily_rates(rows)

    alerts: list[dict[str, Any]] = []
    for camera_id, points in series.items():
        if len(points) < baseline_min_days + 1:
            continue

        baseline_points = points[:-1]
        current_point = points[-1]
        baseline_rate = sum(float(p["recognition_rate"]) for p in baseline_points) / max(
            len(baseline_points), 1
        )
        current_rate = float(current_point["recognition_rate"])

        if baseline_rate <= 0.0:
            continue

        drop_ratio = float((baseline_rate - current_rate) / baseline_rate)
        if drop_ratio < threshold:
            continue

        recent_event = (
            db.query(CameraDriftEvent)
            .filter(
                CameraDriftEvent.camera_id == camera_id,
                CameraDriftEvent.detected_at >= (now - timedelta(hours=24)),
            )
            .order_by(CameraDriftEvent.detected_at.desc())
            .first()
        )
        if recent_event is not None:
            continue

        event = CameraDriftEvent(
            camera_id=camera_id,
            current_rate=current_rate,
            baseline_rate=baseline_rate,
            drop_ratio=drop_ratio,
            threshold=threshold,
            details={
                "window_days": window_days,
                "baseline_days": len(baseline_points),
                "latest_day": current_point.get("day"),
                "series": points,
            },
        )
        db.add(event)
        db.flush()

        alert = {
            "type": "drift_alert",
            "camera_id": camera_id,
            "event_id": int(event.id),
            "current_rate": round(current_rate, 6),
            "baseline_rate": round(baseline_rate, 6),
            "drop_ratio": round(drop_ratio, 6),
            "threshold": threshold,
            "detected_at": event.detected_at.isoformat() if event.detected_at else now.isoformat(),
        }
        alerts.append(alert)

    if alerts:
        try:
            import redis as sync_redis

            r = sync_redis.Redis.from_url(settings.redis_url)
            for alert in alerts:
                r.publish("system:alerts", json.dumps(alert))
            r.close()
        except Exception as exc:
            logger.warning("drift_detector: failed to publish alert to Redis: %s", exc)

    return {
        "status": "completed",
        "window_days": window_days,
        "threshold": threshold,
        "camera_count": len(series),
        "alert_count": len(alerts),
        "alerts": alerts,
    }


async def get_drift_status_async(db: AsyncSession) -> dict[str, Any]:
    """Return drift status payload for admin dashboard and APIs."""
    now = datetime.now(UTC)
    window_days = int(max(settings.drift_window_days, 2))
    window_start = now - timedelta(days=window_days)

    rows_result = await db.execute(
        select(Detection.camera_id, Detection.snapshot_id, Snapshot.timestamp)
        .join(Snapshot, Snapshot.id == Detection.snapshot_id)
        .where(Snapshot.timestamp >= window_start)
    )
    series = _camera_daily_rates(rows_result.all())

    event_rows = await db.execute(
        select(CameraDriftEvent)
        .order_by(CameraDriftEvent.detected_at.desc(), CameraDriftEvent.id.desc())
        .limit(50)
    )
    events = event_rows.scalars().all()

    return {
        "enabled": bool(settings.enable_camera_drift_detection),
        "window_days": window_days,
        "threshold": float(settings.drift_drop_threshold),
        "camera_count": len(series),
        "rates": series,
        "alerts": [
            {
                "id": int(event.id),
                "camera_id": event.camera_id,
                "current_rate": float(event.current_rate),
                "baseline_rate": float(event.baseline_rate),
                "drop_ratio": float(event.drop_ratio),
                "threshold": float(event.threshold),
                "acknowledged": bool(event.acknowledged),
                "detected_at": event.detected_at.isoformat() if event.detected_at else None,
                "details": event.details or {},
            }
            for event in events
        ],
    }
