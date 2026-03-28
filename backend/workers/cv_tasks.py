"""CV processing Celery tasks.

These tasks run in separate Celery worker processes (each with its own GIL)
for true parallelism on CPU-intensive face detection and recognition.

Uses synchronous SQLAlchemy sessions (Celery workers are sync).
"""

from __future__ import annotations

import base64
import logging

import cv2
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import get_settings
from backend.services.ai_pipeline import ai_pipeline, FaceMatch
from backend.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

# Synchronous engine for Celery workers (asyncpg is async-only)
_sync_url = settings.database_url.replace("+asyncpg", "")
_sync_engine = create_engine(_sync_url, pool_size=5, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine)


def _decode_image(image_b64: str) -> np.ndarray | None:
    """Decode a base64-encoded image to BGR numpy array."""
    image_bytes = base64.b64decode(image_b64)
    np_arr = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)


@celery_app.task(
    name="backend.workers.cv_tasks.process_snapshot",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def process_snapshot(
    self,
    image_b64: str,
    device_id: int,
    schedule_id: int,
    camera_id: str,
) -> dict:
    """Process a single snapshot — detect → embed → match → store detections.

    Steps:
    1. Decode image from base64
    2. Run AIPipeline.recognize() (SAHI detection + embedding + cosine match)
    3. Create Snapshot record
    4. Store Detection records (deduplicated within 5-min window)
    5. Publish detection events via Redis for SSE streaming
    """
    image = _decode_image(image_b64)
    if image is None:
        return {"status": "error", "message": "Failed to decode image"}

    with SyncSession() as db:
        from backend.models.attendance import Detection, Snapshot
        from backend.models.student import Student
        from sqlalchemy import and_
        from datetime import timedelta

        # Verify schedule
        from backend.models.course import Schedule

        schedule = db.query(Schedule).filter(Schedule.id == schedule_id).first()
        if schedule is None:
            return {"status": "error", "message": "Schedule not found"}

        # Create snapshot record
        expected = db.query(Student).filter(Student.is_enrolled.is_(True)).count()
        snapshot = Snapshot(
            schedule_id=schedule_id, expected_count=expected
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        # Run AI pipeline
        matches = ai_pipeline.recognize(
            db_session=db, image_bgr=image, schedule_id=schedule_id
        )

        # Dedup within 5-min window
        window_start = snapshot.timestamp - timedelta(minutes=5)
        window_end = snapshot.timestamp + timedelta(minutes=5)
        existing_sightings = {
            row[0]
            for row in (
                db.query(Detection.student_id)
                .join(Snapshot, Snapshot.id == Detection.snapshot_id)
                .filter(
                    and_(
                        Snapshot.schedule_id == schedule_id,
                        Snapshot.timestamp >= window_start,
                        Snapshot.timestamp <= window_end,
                    )
                )
                .all()
            )
        }

        new_detections = 0
        for match in matches:
            if match.student_id in existing_sightings:
                continue
            db.add(
                Detection(
                    snapshot_id=snapshot.id,
                    student_id=match.student_id,
                    confidence=match.confidence,
                    camera_id=camera_id,
                )
            )
            existing_sightings.add(match.student_id)
            new_detections += 1

        db.commit()

    # Annotate image for debug frame store
    annotated = ai_pipeline.annotate_image(image, matches)

    # Publish events to Redis for SSE/WebSocket streaming
    try:
        import redis as sync_redis
        import json

        r = sync_redis.Redis.from_url(settings.redis_url)
        for match in matches:
            if match.student_id not in existing_sightings:
                r.publish(
                    "attendance:detections",
                    json.dumps({
                        "type": "detection",
                        "schedule_id": schedule_id,
                        "student_id": match.student_id,
                        "confidence": round(match.confidence, 4),
                        "snapshot_id": snapshot.id,
                        "camera_id": camera_id,
                    }),
                )
        r.publish(
            "attendance:snapshots",
            json.dumps({
                "type": "snapshot_complete",
                "schedule_id": schedule_id,
                "snapshot_id": snapshot.id,
                "total_detected": len(matches),
                "new_detections": new_detections,
            }),
        )
        r.close()
    except Exception as exc:
        logger.warning("Failed to publish events to Redis: %s", exc)

    return {
        "status": "processed",
        "snapshot_id": snapshot.id,
        "device_id": device_id,
        "camera_id": camera_id,
        "total_detected": len(matches),
        "new_detections": new_detections,
        "annotated_b64": (
            base64.b64encode(annotated).decode("utf-8") if annotated else None
        ),
    }


@celery_app.task(
    name="backend.workers.cv_tasks.process_clip",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def process_clip(
    self,
    clip_b64: str,
    device_id: int,
    schedule_id: int,
    camera_id: str,
) -> dict:
    """Process a video clip — liveness check + multi-frame recognition.

    Steps:
    1. Decode clip and extract frames
    2. Run 3-tier liveness verification
    3. If live, run recognition on sampled frames
    4. Multi-frame voting for robust matching
    """
    clip_bytes = base64.b64decode(clip_b64)

    # Write to temp file for OpenCV VideoCapture
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(clip_bytes)
        temp_path = f.name

    try:
        cap = cv2.VideoCapture(temp_path)
        frames: list[np.ndarray] = []
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
        cap.release()
    finally:
        os.unlink(temp_path)

    if len(frames) < 3:
        return {"status": "error", "message": "Clip too short (need >= 3 frames)"}

    # Sample 5 evenly-spaced frames
    indices = np.linspace(0, len(frames) - 1, min(5, len(frames)), dtype=int)
    sampled_frames = [frames[i] for i in indices]

    # Liveness check
    from backend.services.liveness import check_liveness

    liveness_result = check_liveness(sampled_frames)

    if not liveness_result.is_live:
        return {
            "status": "liveness_failed",
            "message": liveness_result.detail,
            "tier1_score": liveness_result.tier1_motion_score,
            "tier2_magnitude": liveness_result.tier2_flow_magnitude,
            "tier3_spoof_score": liveness_result.tier3_spoof_score,
        }

    # Multi-frame recognition with voting
    with SyncSession() as db:
        from collections import Counter

        all_matches: list[FaceMatch] = []
        for frame in sampled_frames[::2]:  # Process every other sampled frame
            matches = ai_pipeline.recognize(
                db_session=db, image_bgr=frame, schedule_id=schedule_id
            )
            all_matches.extend(matches)

        # Voting: student must appear in >= 2 frames
        student_votes: Counter[int] = Counter()
        student_best: dict[int, FaceMatch] = {}
        for match in all_matches:
            student_votes[match.student_id] += 1
            prev = student_best.get(match.student_id)
            if prev is None or match.confidence > prev.confidence:
                student_best[match.student_id] = match

        confirmed = [
            student_best[sid]
            for sid, votes in student_votes.items()
            if votes >= 2
        ]

        # Store detections
        from backend.models.attendance import Detection, Snapshot
        from backend.models.student import Student

        expected = db.query(Student).filter(Student.is_enrolled.is_(True)).count()
        snapshot = Snapshot(
            schedule_id=schedule_id, expected_count=expected
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        for match in confirmed:
            db.add(
                Detection(
                    snapshot_id=snapshot.id,
                    student_id=match.student_id,
                    confidence=match.confidence,
                    camera_id=camera_id,
                )
            )
        db.commit()

    # Publish events to Redis
    try:
        import redis as sync_redis
        import json

        r = sync_redis.Redis.from_url(settings.redis_url)
        for match in confirmed:
            r.publish(
                "attendance:detections",
                json.dumps({
                    "type": "detection",
                    "schedule_id": schedule_id,
                    "student_id": match.student_id,
                    "confidence": round(match.confidence, 4),
                    "snapshot_id": snapshot.id,
                    "camera_id": camera_id,
                    "liveness": "passed",
                }),
            )
        r.publish(
            "attendance:snapshots",
            json.dumps({
                "type": "snapshot_complete",
                "schedule_id": schedule_id,
                "snapshot_id": snapshot.id,
                "confirmed_students": len(confirmed),
                "liveness": "passed",
            }),
        )
        r.close()
    except Exception as exc:
        logger.warning("Failed to publish clip events to Redis: %s", exc)

    return {
        "status": "processed",
        "liveness": "passed",
        "snapshot_id": snapshot.id,
        "confirmed_students": len(confirmed),
        "total_candidates": len(set(m.student_id for m in all_matches)),
    }
