"""System routes — health check, AI status, dashboard, and settings."""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.api.deps import get_db, require_role
from backend.core.constants import UserRole
from backend.models.attendance import Detection, Snapshot
from backend.models.audit import AuditLog
from backend.models.course import Course, Schedule
from backend.models.room import Device, Room
from backend.models.student import Student
from backend.models.user import User
from backend.schemas.common import HealthResponse
from backend.services.audit_service import log_audit
from backend.services.data_retention import get_retention_status_async
from backend.services.drift_detector import get_drift_status_async
from backend.services.template_refresh import (
    list_template_refresh_logs_async,
    next_auto_refresh_due_date,
    rollback_auto_refresh_async,
    template_age_distribution_async,
)
from backend.services.websocket_manager import ws_manager

router = APIRouter()
logger = logging.getLogger(__name__)

SETTINGS_FILE = Path("backend/data/system_settings.json")
SETTINGS_HISTORY_FILE = Path("backend/data/system_settings_history.json")
LIVE_FPS_WINDOW_SECONDS = 60


def _camera_aliases_for_device(device_id: int) -> set[str]:
    """Return likely camera_id aliases used by ingest clients for one device."""
    raw = str(device_id)
    return {
        raw,
        f"cam-{raw}",
        f"camera-{raw}",
        f"cam_{raw}",
        f"camera_{raw}",
        f"cam{raw}",
        f"camera{raw}",
        f"cam-a{raw}",
        f"camera-a{raw}",
    }


class SystemSettingsUpdate(BaseModel):
    confidence_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    face_match_relaxed_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    face_match_margin: float | None = Field(default=None, ge=0.0, le=0.25)
    lvface_match_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    lvface_match_relaxed_threshold: float | None = Field(default=None, ge=0.5, le=1.0)
    primary_model: str | None = Field(default=None, pattern="^(lvface)$")
    recognition_fusion_mode: str | None = Field(
        default=None,
        pattern="^(lvface_only)$",
    )
    forced_model: str | None = Field(default=None, pattern="^(lvface)$")
    lvface_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    min_face_size_px: int | None = Field(default=None, ge=24, le=256)
    min_face_area_ratio: float | None = Field(default=None, ge=0.0005, le=0.08)
    min_blur_variance: float | None = Field(default=None, ge=5.0, le=500.0)
    min_face_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    detector_confidence_threshold: float | None = Field(default=None, ge=0.05, le=0.95)
    detector_nms_iou_threshold: float | None = Field(default=None, ge=0.1, le=0.95)
    enable_codeformer: bool | None = None
    codeformer_min_face_px: int | None = Field(default=None, ge=16, le=256)
    codeformer_quality_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    codeformer_max_per_frame: int | None = Field(default=None, ge=0, le=20)
    codeformer_fidelity_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    codeformer_identity_preservation_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    min_enrollment_photos: int | None = Field(default=None, ge=3, le=10)
    enable_super_resolution: bool | None = None
    notify_low_attendance: bool | None = None
    notify_liveness_failures: bool | None = None
    notify_device_offline: bool | None = None


class SettingsRollbackRequest(BaseModel):
    revision_id: int = Field(ge=1)


class SettingsRevisionRead(BaseModel):
    id: int
    timestamp: str
    actor_user_id: int | None = None
    action: str
    changes: dict = Field(default_factory=dict)
    source_revision_id: int | None = None
    snapshot: dict = Field(default_factory=dict)


class MultiFaceTestMatchRead(BaseModel):
    student_id: int
    student_name: str
    confidence: float
    quality: float
    bbox: list[int]


class MultiFaceTestDetectionRead(BaseModel):
    bbox: list[int]
    face_size_px: int
    area_ratio: float
    sharpness: float
    quality_score: float
    passes_quality_gate: bool
    reject_reason: str | None = None


class MultiFaceTestRead(BaseModel):
    detected_faces: int
    recognized_faces: int
    unmatched_detected_faces: int
    expected_faces: int
    true_positive: int | None = None
    false_positive: int | None = None
    false_negative: int | None = None
    precision: float | None = None
    recall: float | None = None
    f1_score: float | None = None
    matches: list[MultiFaceTestMatchRead] = Field(default_factory=list)
    detections: list[MultiFaceTestDetectionRead] = Field(default_factory=list)
    quality_reject_summary: dict[str, int] = Field(default_factory=dict)
    missed_expected_students: list[str] = Field(default_factory=list)
    false_positive_students: list[str] = Field(default_factory=list)
    annotated_image_b64: str | None = None
    annotated_detections_image_b64: str | None = None
    notes: list[str] = Field(default_factory=list)


class CameraTrackStatsRead(BaseModel):
    camera_id: str
    active_tracks: int
    confirmed_tracks: int
    average_track_age_seconds: float


async def _load_track_stats_from_redis(redis_url: str) -> list[dict]:
    """Load per-camera tracking diagnostics published by CV workers."""
    try:
        import redis.asyncio as redis_lib

        r = redis_lib.Redis.from_url(redis_url)
        keys = await r.keys("tracking:stats:*")
        payloads: list[dict] = []
        for key in keys:
            raw = await r.get(key)
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if isinstance(item, dict) and item.get("camera_id"):
                payloads.append(item)
        await r.aclose()
        return payloads
    except Exception as exc:
        logger.debug("system.tracks: redis stats unavailable: %s", exc)
        return []


async def _load_cross_camera_stats_from_redis(redis_url: str) -> dict:
    """Load cross-camera linking diagnostics published by CV workers."""
    try:
        import redis.asyncio as redis_lib

        r = redis_lib.Redis.from_url(redis_url)
        raw = await r.get("tracking:cross_camera_stats")
        await r.aclose()
        if not raw:
            return {}
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        logger.debug("system.tracks: cross-camera redis stats unavailable: %s", exc)
        return {}


def _default_system_settings() -> dict:
    settings = get_settings()
    primary_model = "lvface"
    return {
        "confidence_threshold": float(settings.face_match_threshold),
        "face_match_relaxed_threshold": float(settings.face_match_relaxed_threshold),
        "face_match_margin": float(settings.face_match_margin),
        "lvface_match_threshold": float(settings.lvface_match_threshold),
        "lvface_match_relaxed_threshold": float(settings.lvface_match_relaxed_threshold),
        "primary_model": primary_model,
        "recognition_fusion_mode": "lvface_only",
        "forced_model": "lvface",
        "lvface_weight": 1.0,
        "min_face_size_px": int(settings.min_face_size_px),
        "min_face_area_ratio": float(settings.min_face_area_ratio),
        "min_blur_variance": float(settings.min_blur_variance),
        "min_face_quality_score": float(settings.min_face_quality_score),
        "detector_confidence_threshold": float(settings.detector_confidence_threshold),
        "detector_nms_iou_threshold": float(settings.detector_nms_iou_threshold),
        "enable_codeformer": bool(settings.enable_codeformer),
        "codeformer_min_face_px": int(settings.codeformer_min_face_px),
        "codeformer_quality_threshold": float(settings.codeformer_quality_threshold),
        "codeformer_max_per_frame": int(settings.codeformer_max_per_frame),
        "codeformer_fidelity_weight": float(settings.codeformer_fidelity_weight),
        "codeformer_identity_preservation_threshold": float(
            settings.codeformer_identity_preservation_threshold
        ),
        "min_enrollment_photos": 5,
        "enable_super_resolution": bool(settings.enable_super_resolution),
        "notify_low_attendance": True,
        "notify_liveness_failures": True,
        "notify_device_offline": True,
    }


def _load_system_settings() -> dict:
    defaults = _default_system_settings()
    if not SETTINGS_FILE.exists():
        return defaults

    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    merged = defaults.copy()
    for key in defaults:
        if key in raw:
            merged[key] = raw[key]
    return merged


def _save_system_settings(payload: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_settings_history() -> list[dict]:
    if not SETTINGS_HISTORY_FILE.exists():
        return []
    try:
        raw = json.loads(SETTINGS_HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return raw if isinstance(raw, list) else []


def _save_settings_history(history: list[dict]) -> None:
    SETTINGS_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_HISTORY_FILE.write_text(
        json.dumps(history[-200:], indent=2), encoding="utf-8"
    )


def _append_settings_revision(entry: dict) -> None:
    history = _load_settings_history()
    history.append(entry)
    _save_settings_history(history)


def _next_revision_id(history: list[dict]) -> int:
    max_id = 0
    for item in history:
        try:
            max_id = max(max_id, int(item.get("id", 0)))
        except Exception:
            continue
    return max_id + 1


def _settings_response(payload: dict) -> dict:
    settings = get_settings()
    return {
        **payload,
        "access_token_ttl_minutes": settings.jwt_access_token_expire_minutes,
        "refresh_token_ttl_days": settings.jwt_refresh_token_expire_days,
        "hmac_device_auth_enabled": True,
        "rate_limiting_enabled": True,
    }


def _load_latest_fairness_audit() -> dict:
    cfg = get_settings()
    latest_path = Path(cfg.fairness_audit_output_dir) / "fairness_audit_latest.json"
    if not latest_path.exists():
        return {
            "available": False,
            "path": str(latest_path),
            "report": None,
        }

    try:
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("system.fairness: failed to load latest report: %s", exc)
        return {
            "available": False,
            "path": str(latest_path),
            "report": None,
        }

    return {
        "available": True,
        "path": str(latest_path),
        "report": payload,
    }


def _floor_to_bucket(ts: datetime, bucket_minutes: int) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    floored_minute = (ts.minute // bucket_minutes) * bucket_minutes
    return ts.replace(minute=floored_minute, second=0, microsecond=0)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health_check(db: AsyncSession = Depends(get_db)):
    """Check the health of all dependencies."""
    # Check database
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"

    # Check Redis
    redis_status = "ok"
    try:
        import redis.asyncio as redis_lib
        from backend.core.config import get_settings
        settings = get_settings()
        r = redis_lib.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
    except Exception:
        redis_status = "error"

    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return HealthResponse(status=overall, database=db_status, redis=redis_status)


@router.get(
    "/ai/status",
    summary="AI pipeline readiness check",
)
async def ai_status(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Check if AI models are loaded and ready."""
    from backend.services.ai_pipeline import ai_pipeline

    return ai_pipeline.readiness()


@router.get(
    "/inference-stats",
    summary="Inference batching and latency stats",
)
async def inference_stats_summary(
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return rolling inference stats for Triton/local fallback observability."""
    from backend.services.inference_stats import inference_stats

    snapshot = inference_stats.snapshot()
    cfg = get_settings()
    snapshot.update(
        {
            "triton_enabled": bool(cfg.enable_triton),
            "triton_url": cfg.triton_url,
        }
    )
    return snapshot


@router.get(
    "/tracks",
    summary="Temporal tracking diagnostics",
)
async def track_stats_summary(
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return per-camera active/confirmed track counts and average track age."""
    from backend.services.cross_camera import cross_camera_linker
    from backend.services.tracker import tracker_manager

    cfg = get_settings()
    local_stats = tracker_manager.collect_diagnostics()
    redis_stats = await _load_track_stats_from_redis(cfg.redis_url)
    cross_camera_redis_stats = await _load_cross_camera_stats_from_redis(cfg.redis_url)
    cross_camera_local_stats = cross_camera_linker.metrics_snapshot()

    merged_by_camera: dict[str, dict] = {}
    for item in local_stats:
        camera_id = str(item.get("camera_id", "")).strip()
        if not camera_id:
            continue
        merged_by_camera[camera_id] = {
            "camera_id": camera_id,
            "active_tracks": int(item.get("active_tracks", 0)),
            "confirmed_tracks": int(item.get("confirmed_tracks", 0)),
            "average_track_age_seconds": float(item.get("average_track_age_seconds", 0.0)),
        }

    for item in redis_stats:
        camera_id = str(item.get("camera_id", "")).strip()
        if not camera_id:
            continue
        merged_by_camera[camera_id] = {
            "camera_id": camera_id,
            "active_tracks": int(item.get("active_tracks", 0)),
            "confirmed_tracks": int(item.get("confirmed_tracks", 0)),
            "average_track_age_seconds": float(item.get("average_track_age_seconds", 0.0)),
        }

    cameras = [
        CameraTrackStatsRead(**payload).model_dump()
        for payload in sorted(merged_by_camera.values(), key=lambda item: item["camera_id"])
    ]
    active_total = sum(int(item["active_tracks"]) for item in cameras)
    confirmed_total = sum(int(item["confirmed_tracks"]) for item in cameras)

    return {
        "tracking_enabled": bool(cfg.enable_tracking),
        "cross_camera_reid_enabled": bool(cfg.enable_cross_camera_reid),
        "camera_count": int(len(cameras)),
        "active_tracks": int(active_total),
        "confirmed_tracks": int(confirmed_total),
        "cross_camera": {
            "link_count": int(
                cross_camera_redis_stats.get(
                    "link_count",
                    cross_camera_local_stats.get("link_count", 0),
                )
            ),
            "rejected_link_count": int(
                cross_camera_redis_stats.get(
                    "rejected_link_count",
                    cross_camera_local_stats.get("rejected_link_count", 0),
                )
            ),
            "confidence_distribution": (
                cross_camera_redis_stats.get("confidence_distribution")
                or cross_camera_local_stats.get("confidence_distribution")
                or {
                    "0.0-0.4": 0,
                    "0.4-0.6": 0,
                    "0.6-0.8": 0,
                    "0.8-1.0": 0,
                }
            ),
        },
        "cameras": cameras,
    }


@router.get(
    "/drift-status",
    summary="Camera drift detection status",
)
async def drift_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return camera drift metrics and recent drift alert events."""
    return await get_drift_status_async(db)


@router.get(
    "/retention-status",
    summary="Data retention compliance status",
)
async def retention_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return pending retention workload and next purge window."""
    return await get_retention_status_async(db)


@router.get(
    "/fairness-audit/latest",
    summary="Latest fairness audit report",
)
async def latest_fairness_audit(
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return latest fairness audit JSON if available."""
    return _load_latest_fairness_audit()


@router.get(
    "/template-refresh/logs",
    summary="Template auto-refresh activity log",
)
async def template_refresh_logs(
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return recent template refresh and rollback audit events."""
    rows = await list_template_refresh_logs_async(db, limit=limit)
    return {"count": len(rows), "items": rows}


@router.post(
    "/template-refresh/{audit_log_id}/rollback",
    summary="Rollback a specific auto template refresh",
)
async def rollback_template_refresh(
    audit_log_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Admin-only rollback for one refresh event."""
    try:
        result = await rollback_auto_refresh_async(
            db,
            audit_log_id=audit_log_id,
            actor=f"manual:{admin_user.id}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await log_audit(
        db,
        user_id=admin_user.id,
        action="template.refresh.rollback",
        resource="template_audit_log",
        details={"audit_log_id": int(audit_log_id), **result},
    )
    return result


@router.get(
    "/governance/overview",
    summary="Governance dashboard overview",
)
async def governance_overview(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Aggregate fairness, template lifecycle, retention, and drift status."""
    fairness = _load_latest_fairness_audit()
    template_histogram = await template_age_distribution_async(db)
    refresh_log = await list_template_refresh_logs_async(db, limit=20)
    retention = await get_retention_status_async(db)
    drift = await get_drift_status_async(db)

    return {
        "fairness": fairness,
        "template_age_histogram": template_histogram,
        "template_refresh": {
            "next_auto_refresh_due": next_auto_refresh_due_date(),
            "recent": refresh_log,
        },
        "retention": retention,
        "drift": drift,
    }


@router.get(
    "/dashboard/summary",
    summary="Dashboard summary metrics",
)
async def dashboard_summary(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return top-level dashboard summary cards."""
    now = datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_students = (await db.execute(select(func.count(Student.id)))).scalar() or 0

    present_today = (
        await db.execute(
            select(func.count(func.distinct(Detection.student_id)))
            .join(Snapshot, Detection.snapshot_id == Snapshot.id)
            .where(Snapshot.timestamp >= start_of_day)
        )
    ).scalar() or 0

    active_from_db = (
        await db.execute(select(func.count(Device.id)).where(Device.ws_session_id.is_not(None)))
    ).scalar() or 0
    active_cameras = max(active_from_db, ws_manager.connected_count)

    liveness_failures = (
        await db.execute(
            select(func.count(AuditLog.id)).where(
                AuditLog.created_at >= start_of_day,
                func.lower(AuditLog.action).like("%liveness%"),
            )
        )
    ).scalar() or 0

    attendance_rate = round((present_today / total_students) * 100, 1) if total_students else 0.0

    return {
        "total_students": int(total_students),
        "present_today": int(present_today),
        "active_cameras": int(active_cameras),
        "liveness_failures": int(liveness_failures),
        "attendance_rate": attendance_rate,
    }


@router.get(
    "/dashboard/trend",
    summary="Attendance trend points",
)
async def dashboard_trend(
    hours: int = Query(default=4, ge=1, le=24),
    bucket_minutes: int = Query(default=30, ge=5, le=60),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return attendance trend series for charting."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=hours)
    aligned_start = _floor_to_bucket(window_start, bucket_minutes)
    bucket_delta = timedelta(minutes=bucket_minutes)

    buckets = {}
    current = aligned_start
    while current <= now:
        buckets[current] = 0
        current += bucket_delta

    rows = await db.execute(
        select(
            Snapshot.timestamp,
            func.count(func.distinct(Detection.student_id)).label("present"),
        )
        .join(Detection, Detection.snapshot_id == Snapshot.id)
        .where(Snapshot.timestamp >= aligned_start)
        .group_by(Snapshot.id, Snapshot.timestamp)
        .order_by(Snapshot.timestamp.asc())
    )

    for row in rows:
        ts = row.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        bucket_key = _floor_to_bucket(ts, bucket_minutes)
        if bucket_key in buckets:
            buckets[bucket_key] = max(buckets[bucket_key], int(row.present or 0))

    return [
        {"time": key.strftime("%H:%M"), "present": value}
        for key, value in buckets.items()
    ]


@router.get(
    "/dashboard/recent-detections",
    summary="Recent detection feed for dashboard",
)
async def dashboard_recent_detections(
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return latest detections with student and course context."""
    rows = await db.execute(
        select(
            Detection.id,
            Student.name,
            Course.code,
            Snapshot.timestamp,
            Detection.confidence,
        )
        .join(Student, Detection.student_id == Student.id)
        .join(Snapshot, Detection.snapshot_id == Snapshot.id)
        .join(Schedule, Snapshot.schedule_id == Schedule.id)
        .join(Course, Schedule.course_id == Course.id)
        .order_by(Snapshot.timestamp.desc())
        .offset(offset)
        .limit(limit)
    )

    results = []
    for row in rows:
        results.append(
            {
                "id": row.id,
                "name": row.name,
                "course": row.code,
                "time": row.timestamp.isoformat() if row.timestamp else None,
                "confidence": float(row.confidence),
            }
        )
    return results


@router.get(
    "/live/cameras",
    summary="Live camera status",
)
async def live_cameras(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return camera status cards for the live feed page."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=LIVE_FPS_WINDOW_SECONDS)
    fps_rows = await db.execute(
        select(
            Detection.camera_id,
            func.count(func.distinct(Detection.snapshot_id)).label("snapshot_hits"),
            func.count(Detection.id).label("detection_hits"),
        )
        .join(Snapshot, Detection.snapshot_id == Snapshot.id)
        .where(Snapshot.timestamp >= cutoff)
        .group_by(Detection.camera_id)
    )

    camera_metrics: dict[str, dict[str, float]] = {}
    for camera_id, snapshot_hits, detection_hits in fps_rows:
        key = str(camera_id or "").strip().lower()
        if not key:
            continue
        snapshot_rate = float(snapshot_hits or 0) / float(LIVE_FPS_WINDOW_SECONDS)
        detection_rate = (float(detection_hits or 0) * 60.0) / float(
            LIVE_FPS_WINDOW_SECONDS
        )
        camera_metrics[key] = {
            "fps": round(snapshot_rate, 2),
            "detections_per_min": round(detection_rate, 1),
        }

    rows = await db.execute(
        select(Device.id, Device.room_id, Device.ws_session_id, Room.room_name)
        .join(Room, Device.room_id == Room.id, isouter=True)
        .order_by(Device.id.asc())
    )

    connected_ids = set(ws_manager.active_devices.keys())
    cameras = []
    for row in rows:
        is_active = bool(row.ws_session_id) or int(row.id) in connected_ids

        metrics = None
        for alias in _camera_aliases_for_device(int(row.id)):
            metrics = camera_metrics.get(alias.lower())
            if metrics is not None:
                break

        cameras.append(
            {
                "id": f"cam-{row.id}",
                "room": row.room_name or f"Room #{row.room_id}",
                "status": "active" if is_active else "offline",
                "fps": float(metrics["fps"]) if metrics else 0.0,
                "detections_per_min": (
                    float(metrics["detections_per_min"]) if metrics else 0.0
                ),
            }
        )
    return cameras


@router.get(
    "/live/stats/{schedule_id}",
    summary="Live session stats for a schedule",
)
async def live_stats(
    schedule_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return live counters for detections/snapshots and liveness fails."""
    snapshots = (
        await db.execute(select(func.count(Snapshot.id)).where(Snapshot.schedule_id == schedule_id))
    ).scalar() or 0

    detections = (
        await db.execute(
            select(func.count(Detection.id))
            .join(Snapshot, Detection.snapshot_id == Snapshot.id)
            .where(Snapshot.schedule_id == schedule_id)
        )
    ).scalar() or 0

    liveness_failures = (
        await db.execute(
            select(func.count(AuditLog.id)).where(
                func.lower(AuditLog.action).like("%liveness%")
            )
        )
    ).scalar() or 0

    return {
        "schedule_id": schedule_id,
        "detections": int(detections),
        "snapshots": int(snapshots),
        "liveness_failures": int(liveness_failures),
    }


@router.get(
    "/settings",
    summary="Get system UI settings",
)
async def get_system_settings(
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Fetch persistent system settings for admin UI."""
    return _settings_response(_load_system_settings())


@router.patch(
    "/settings",
    summary="Update system UI settings",
)
async def update_system_settings(
    body: SystemSettingsUpdate,
    admin: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Persist admin-editable settings from the settings page."""
    current = _load_system_settings()
    updates = body.model_dump(exclude_unset=True)
    previous = current.copy()
    current.update(updates)

    strict = float(current.get("confidence_threshold", 0.85))
    relaxed = float(current.get("face_match_relaxed_threshold", strict))
    if relaxed > strict:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="face_match_relaxed_threshold must be <= confidence_threshold",
        )

    lv_strict = float(current.get("lvface_match_threshold", strict))
    lv_relaxed = float(current.get("lvface_match_relaxed_threshold", lv_strict))
    if lv_relaxed > lv_strict:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="lvface_match_relaxed_threshold must be <= lvface_match_threshold",
        )

    lv_w = float(current.get("lvface_weight", 1.0))
    if lv_w <= 0.0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="lvface_weight must be > 0",
        )

    # Enforce LVFace-only runtime policy regardless of payload aliases.
    current["primary_model"] = "lvface"
    current["recognition_fusion_mode"] = "lvface_only"
    current["forced_model"] = "lvface"
    current["lvface_weight"] = 1.0

    _save_system_settings(current)

    history = _load_settings_history()
    revision = {
        "id": _next_revision_id(history),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_user_id": admin.id,
        "action": "update",
        "changes": updates,
        "snapshot": current,
    }
    _append_settings_revision(revision)

    await log_audit(
        db,
        user_id=admin.id,
        action="settings.update",
        resource="system_settings",
        details={
            "changes": updates,
            "previous": previous,
            "current": current,
            "revision_id": revision["id"],
        },
    )

    return _settings_response(current)


@router.get(
    "/settings/history",
    response_model=list[SettingsRevisionRead],
    summary="Get settings revision history",
)
async def get_settings_history(
    limit: int = Query(default=20, ge=1, le=100),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """Return latest settings revisions, newest first."""
    history = _load_settings_history()
    latest = list(reversed(history[-limit:]))
    return latest


@router.post(
    "/settings/rollback",
    summary="Rollback settings to a previous revision",
)
async def rollback_system_settings(
    body: SettingsRollbackRequest,
    admin: User = Depends(require_role(UserRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    """Restore settings from a selected revision snapshot."""
    history = _load_settings_history()
    target = next((r for r in history if int(r.get("id", 0)) == body.revision_id), None)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settings revision not found",
        )

    snapshot = target.get("snapshot") or {}
    if not snapshot:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revision has no snapshot payload",
        )

    previous = _load_system_settings()
    _save_system_settings(snapshot)

    rollback_revision = {
        "id": _next_revision_id(history),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_user_id": admin.id,
        "action": "rollback",
        "changes": {},
        "source_revision_id": body.revision_id,
        "snapshot": snapshot,
    }
    _append_settings_revision(rollback_revision)

    await log_audit(
        db,
        user_id=admin.id,
        action="settings.rollback",
        resource="system_settings",
        details={
            "source_revision_id": body.revision_id,
            "rollback_revision_id": rollback_revision["id"],
            "previous": previous,
            "current": snapshot,
        },
    )

    return {
        "message": "Settings rolled back",
        "revision_id": rollback_revision["id"],
        "settings": _settings_response(snapshot),
    }


@router.post(
    "/testing/multi-face",
    response_model=MultiFaceTestRead,
    summary="Run multi-face classroom test on one image",
)
async def test_multi_face_scene(
    image: UploadFile = File(...),
    expected_student_ids: list[int] = Form(default_factory=list),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Run a classroom-style multi-face recognition test on a single captured image.

    This endpoint is for manual field validation. Users can optionally supply
    expected student IDs to calculate precision/recall/F1 for quick accuracy checks.
    """
    import cv2
    import numpy as np

    from backend.services.ai_pipeline import ai_pipeline

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Test image is empty")

    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise HTTPException(status_code=400, detail="Invalid image format")

    runtime_gates = ai_pipeline.get_runtime_gates()
    detected_boxes = ai_pipeline.detect_faces_sahi(decoded)

    def _reject_reason(*, face_size_px: int, sharpness: float, quality_score: float) -> str | None:
        min_face_size_px = int(runtime_gates["min_face_size_px"])
        min_blur_variance = float(runtime_gates["min_blur_variance"])
        min_face_quality_score = float(runtime_gates["min_face_quality_score"])
        if face_size_px < min_face_size_px:
            return f"face_too_small (< {min_face_size_px}px)"
        if sharpness < min_blur_variance:
            return f"image_too_blurry (< {min_blur_variance:.1f})"
        if quality_score < min_face_quality_score:
            return f"face_quality_low (< {min_face_quality_score:.2f})"
        return None

    detection_payload: list[MultiFaceTestDetectionRead] = []
    reject_summary: dict[str, int] = {}
    frame_h, frame_w = decoded.shape[:2]

    detection_canvas = decoded.copy()
    for box in detected_boxes:
        x, y, w, h = [int(v) for v in box]
        x1 = max(x, 0)
        y1 = max(y, 0)
        x2 = min(x + w, frame_w)
        y2 = min(y + h, frame_h)
        if x2 <= x1 or y2 <= y1:
            continue

        crop = decoded[y1:y2, x1:x2]
        quality_score, sharpness = ai_pipeline.face_quality_score(
            crop_bgr=crop,
            bbox=(x, y, w, h),
            full_image_shape=decoded.shape,
            runtime_gates=runtime_gates,
        )

        passes_gate = ai_pipeline._is_face_usable(
            (x, y, w, h),
            quality_score=float(quality_score),
            sharpness=float(sharpness),
            runtime_gates=runtime_gates,
        )
        face_size_px = int(min(w, h))
        area_ratio = float((w * h) / max(frame_h * frame_w, 1))
        reject_reason = None if passes_gate else _reject_reason(
            face_size_px=face_size_px,
            sharpness=float(sharpness),
            quality_score=float(quality_score),
        )

        if reject_reason:
            reject_summary[reject_reason] = int(reject_summary.get(reject_reason, 0) + 1)

        detection_payload.append(
            MultiFaceTestDetectionRead(
                bbox=[x, y, w, h],
                face_size_px=face_size_px,
                area_ratio=area_ratio,
                sharpness=float(sharpness),
                quality_score=float(quality_score),
                passes_quality_gate=bool(passes_gate),
                reject_reason=reject_reason,
            )
        )

        box_color = (0, 180, 0) if passes_gate else (0, 80, 255)
        cv2.rectangle(detection_canvas, (x1, y1), (x2, y2), box_color, 2)
        label = (
            f"F{len(detection_payload)} {'PASS' if passes_gate else 'REJECT'} "
            f"q:{float(quality_score):.2f} s:{float(sharpness):.1f}"
        )
        cv2.putText(
            detection_canvas,
            label,
            (x1, max(y1 - 10, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            box_color,
            2,
        )

    def _recognize(sync_session):
        from backend.models.student import Student

        matches = ai_pipeline.recognize(
            db_session=sync_session,
            image_bgr=decoded,
            schedule_id=0,
        )
        ids = [int(match.student_id) for match in matches]
        name_map: dict[int, str] = {}
        if ids:
            rows = sync_session.execute(
                select(Student.id, Student.name).where(Student.id.in_(ids))
            ).all()
            name_map = {int(row.id): str(row.name) for row in rows}
        return matches, name_map

    raw_matches, recognized_names = await db.run_sync(_recognize)

    sorted_matches = sorted(raw_matches, key=lambda item: float(item.confidence), reverse=True)
    recognized_ids = {int(match.student_id) for match in sorted_matches}
    expected_ids = {
        int(student_id)
        for student_id in expected_student_ids
        if int(student_id) > 0
    }

    union_ids = expected_ids | recognized_ids
    id_to_name: dict[int, str] = {}
    if union_ids:
        rows = await db.execute(
            select(Student.id, Student.name).where(Student.id.in_(list(union_ids)))
        )
        id_to_name = {int(row.id): str(row.name) for row in rows.all()}

    matches_payload = [
        MultiFaceTestMatchRead(
            student_id=int(match.student_id),
            student_name=id_to_name.get(int(match.student_id), recognized_names.get(int(match.student_id), f"Student {int(match.student_id)}")),
            confidence=float(match.confidence),
            quality=float(match.quality),
            bbox=[int(v) for v in match.bbox],
        )
        for match in sorted_matches
    ]

    tp = fp = fn = None
    precision = recall = f1_score = None
    missed_expected: list[str] = []
    false_positive: list[str] = []

    if expected_ids:
        tp = int(len(expected_ids & recognized_ids))
        fp = int(len(recognized_ids - expected_ids))
        fn = int(len(expected_ids - recognized_ids))

        precision = float(tp / max(len(recognized_ids), 1))
        recall = float(tp / max(len(expected_ids), 1))
        denom = precision + recall
        f1_score = float((2 * precision * recall / denom) if denom > 0 else 0.0)

        missed_expected = [
            id_to_name.get(sid, f"Student {sid}")
            for sid in sorted(expected_ids - recognized_ids)
        ]
        false_positive = [
            id_to_name.get(sid, f"Student {sid}")
            for sid in sorted(recognized_ids - expected_ids)
        ]

    annotated_bytes = ai_pipeline.annotate_image(decoded, sorted_matches)
    annotated_b64 = (
        base64.b64encode(annotated_bytes).decode("utf-8")
        if annotated_bytes
        else None
    )

    ok, detection_encoded = cv2.imencode(".jpg", detection_canvas)
    detection_b64 = (
        base64.b64encode(detection_encoded.tobytes()).decode("utf-8")
        if ok
        else None
    )

    notes = [
        "Use a well-lit image with all faces visible for more stable results.",
        "Detected faces include all boxes; recognized faces are those passing quality and matching thresholds.",
    ]
    if detection_payload:
        passed_quality = sum(1 for item in detection_payload if item.passes_quality_gate)
        notes.append(
            f"{passed_quality}/{len(detection_payload)} detected faces passed quality gates before matching."
        )
    if detected_boxes and not sorted_matches:
        notes.append(
            "No faces matched. Check detection diagnostics below to see if gates are rejecting faces, then tune blur/size/quality thresholds in Settings."
        )
    if not expected_ids:
        notes.append("Select expected students in the UI to compute precision/recall/F1.")

    await log_audit(
        db,
        user_id=user.id,
        action="system.testing.multi_face",
        resource="ai_pipeline",
        details={
            "detected_faces": int(len(detected_boxes)),
            "recognized_faces": int(len(sorted_matches)),
            "quality_passed_faces": int(sum(1 for item in detection_payload if item.passes_quality_gate)),
            "quality_reject_summary": reject_summary,
            "expected_faces": int(len(expected_ids)),
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1_score": f1_score,
        },
    )

    return MultiFaceTestRead(
        detected_faces=int(len(detected_boxes)),
        recognized_faces=int(len(sorted_matches)),
        unmatched_detected_faces=max(int(len(detected_boxes) - len(sorted_matches)), 0),
        expected_faces=int(len(expected_ids)),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        precision=precision,
        recall=recall,
        f1_score=f1_score,
        matches=matches_payload,
        detections=detection_payload,
        quality_reject_summary=reject_summary,
        missed_expected_students=missed_expected,
        false_positive_students=false_positive,
        annotated_image_b64=annotated_b64,
        annotated_detections_image_b64=detection_b64,
        notes=notes,
    )


@router.get(
    "/audit-logs",
    summary="View audit logs (admin only)",
)
async def list_audit_logs(
    skip: int = 0,
    limit: int = 100,
    action: str | None = None,
    resource: str | None = None,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role(UserRole.ADMIN)),
):
    """List audit logs with optional filters."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    if action:
        query = query.where(AuditLog.action == action)
    if resource:
        query = query.where(AuditLog.resource == resource)
    result = await db.execute(query)
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource": log.resource,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
