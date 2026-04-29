"""CV processing Celery tasks.

These tasks run in separate Celery worker processes (each with its own GIL)
for true parallelism on CPU-intensive face detection and recognition.

Uses synchronous SQLAlchemy sessions (Celery workers are sync).
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.core.config import get_settings
from backend.services.ai_pipeline import ai_pipeline, FaceMatch
from backend.services.cross_camera import cross_camera_linker
from backend.services.reid import reid_service
from backend.services.tracker import Track, tracker_manager
from backend.services.template_refresh import maybe_auto_refresh_template_sync
from backend.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()

# Synchronous engine for Celery workers (asyncpg is async-only)
_sync_url = settings.database_url.replace("+asyncpg", "")
_sync_engine = create_engine(_sync_url, pool_size=5, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine)


@dataclass
class _TemplateBank:
    matrix: np.ndarray
    student_rows: dict[int, list[int]]
    retention_scores: np.ndarray


@dataclass
class _TrackMatch:
    student_id: int
    confidence: float
    bbox: tuple[int, int, int, int]
    quality: float
    track_id: int | None
    cross_camera_source_track_id: int | None = None


def _tracking_quality_fn(
    crop_bgr: np.ndarray,
    bbox: tuple[int, int, int, int],
    frame_shape: tuple[int, int, int],
) -> tuple[float, float]:
    """Route tracker quality scoring through the pipeline's existing formula."""
    return ai_pipeline.face_quality_score(
        crop_bgr=crop_bgr,
        bbox=bbox,
        full_image_shape=frame_shape,
    )


tracker_manager.configure(
    quality_fn=_tracking_quality_fn,
    top_n_frames=settings.tracking_top_n_frames,
    max_lost_frames=settings.tracking_max_lost_frames,
    consistent_match_count=settings.tracking_consistent_match_count,
    quality_drop_ratio=settings.tracking_quality_drop_ratio,
)


def _build_template_banks(db_session: Session) -> dict[str, _TemplateBank]:
    """Build per-model template banks once per snapshot processing call."""
    banks: dict[str, _TemplateBank] = {}
    for model_name in ("arcface", "adaface", "lvface"):
        _, matrix, student_rows, retention_scores = ai_pipeline._build_template_matrix(  # noqa: SLF001
            db_session,
            model_name=model_name,
        )
        banks[model_name] = _TemplateBank(
            matrix=matrix,
            student_rows=student_rows,
            retention_scores=retention_scores,
        )
    return banks


def _identify_track_from_best_frame(
    track: Track,
    runtime_gates: dict,
    template_banks: dict[str, _TemplateBank],
) -> tuple[_TrackMatch | None, np.ndarray | None]:
    """Run one identification pass on the best buffered frame for a track."""
    best_frame = track.best_frame
    if best_frame is None:
        return None, None

    bbox = best_frame.bbox
    quality_score = float(best_frame.quality_score)
    sharpness = float(best_frame.sharpness)

    if not ai_pipeline._is_face_usable(  # noqa: SLF001
        bbox,
        quality_score=quality_score,
        sharpness=sharpness,
        runtime_gates=runtime_gates,
    ):
        return None, None

    crop = best_frame.crop_bgr
    arcface_probe = (
        ai_pipeline.extract_embedding(crop)
        if template_banks["arcface"].matrix.shape[0] > 0
        else None
    )
    adaface_probe = (
        ai_pipeline.extract_embedding_adaface(crop)
        if template_banks["adaface"].matrix.shape[0] > 0
        else None
    )
    lvface_probe = (
        ai_pipeline.extract_embedding_lvface(crop)
        if template_banks["lvface"].matrix.shape[0] > 0
        else None
    )

    if arcface_probe is None and adaface_probe is None and lvface_probe is None:
        return None, None

    model_scores: dict[str, dict[int, float]] = {
        "arcface": ai_pipeline._score_per_student(  # noqa: SLF001
            probe=arcface_probe,
            matrix=template_banks["arcface"].matrix,
            student_rows=template_banks["arcface"].student_rows,
            retention_scores=template_banks["arcface"].retention_scores,
        ),
        "adaface": ai_pipeline._score_per_student(  # noqa: SLF001
            probe=adaface_probe,
            matrix=template_banks["adaface"].matrix,
            student_rows=template_banks["adaface"].student_rows,
            retention_scores=template_banks["adaface"].retention_scores,
        ),
        "lvface": ai_pipeline._score_per_student(  # noqa: SLF001
            probe=lvface_probe,
            matrix=template_banks["lvface"].matrix,
            student_rows=template_banks["lvface"].student_rows,
            retention_scores=template_banks["lvface"].retention_scores,
        ),
    }

    available_models = {
        model_name
        for model_name, scores in model_scores.items()
        if bool(scores)
    }
    if not available_models:
        return None, None

    fusion_mode = str(runtime_gates.get("recognition_fusion_mode", "weighted_average"))
    forced_model = runtime_gates.get("forced_model")

    if isinstance(forced_model, str) and forced_model in available_models:
        selected_models = {forced_model}
    elif fusion_mode in {"arcface_only", "adaface_only", "lvface_only"}:
        requested = fusion_mode.replace("_only", "")
        selected_models = {requested} if requested in available_models else set()
    else:
        selected_models = set(available_models)

    if not selected_models:
        return None, None

    fusion_weights = ai_pipeline.model_fusion_weights(
        runtime_gates,
        available_models=selected_models,
    )

    best_student_id: int | None = None
    best_score = -1.0
    second_best = -1.0
    best_decision_model: str | None = None
    best_model_value_pairs: list[tuple[str, float]] = []

    candidate_ids: set[int] = set()
    for model_name in selected_models:
        candidate_ids.update(model_scores[model_name].keys())

    for sid in candidate_ids:
        model_value_pairs = [
            (model_name, float(model_scores[model_name][sid]))
            for model_name in selected_models
            if sid in model_scores[model_name]
        ]
        if not model_value_pairs:
            continue

        combined_score = -1.0
        decision_model = None
        if fusion_mode == "max_confidence" and forced_model is None:
            decision_model, combined_score = max(
                model_value_pairs,
                key=lambda item: item[1],
            )
        else:
            weighted_sum = 0.0
            total_weight = 0.0
            for model_name, score in model_value_pairs:
                model_weight = float(fusion_weights.get(model_name, 0.0))
                if model_weight <= 0.0:
                    continue
                weighted_sum += model_weight * score
                total_weight += model_weight

            if total_weight <= 0.0:
                continue

            combined_score = float(weighted_sum / total_weight)
            if len(model_value_pairs) == 1:
                decision_model = model_value_pairs[0][0]

        if combined_score > best_score:
            second_best = best_score
            best_score = combined_score
            best_student_id = sid
            best_decision_model = decision_model
            best_model_value_pairs = model_value_pairs
        elif combined_score > second_best:
            second_best = combined_score

    if best_student_id is None:
        return None, None

    if not ai_pipeline._match_decision(  # noqa: SLF001
        best_score,
        second_best,
        decision_model=best_decision_model,
        runtime_gates=runtime_gates,
    ):
        return None, None

    probes_by_model = {
        "arcface": arcface_probe,
        "adaface": adaface_probe,
        "lvface": lvface_probe,
    }
    selected_probe: np.ndarray | None = None
    if best_decision_model is not None:
        selected_probe = probes_by_model.get(best_decision_model)
    if selected_probe is None and best_model_value_pairs:
        top_model = max(best_model_value_pairs, key=lambda item: item[1])[0]
        selected_probe = probes_by_model.get(top_model)

    return (
        _TrackMatch(
            student_id=int(best_student_id),
            confidence=float(best_score),
            bbox=bbox,
            quality=quality_score,
            track_id=int(track.track_id),
        ),
        selected_probe,
    )


def _dedupe_matches_by_student(matches: list[Any]) -> list[Any]:
    """Keep highest-confidence detection per student for one snapshot."""
    best_per_student: dict[int, Any] = {}
    for match in matches:
        student_id = int(match.student_id)
        prev = best_per_student.get(student_id)
        if prev is None or float(match.confidence) > float(prev.confidence):
            best_per_student[student_id] = match
    return list(best_per_student.values())


def _ensure_track_person_embedding(track: Track) -> np.ndarray | None:
    """Ensure track has a person-level ReID embedding from best buffered frame."""
    if track.best_person_embedding is not None:
        return track.best_person_embedding

    best_frame = track.best_frame
    if best_frame is None:
        return None

    person_emb = reid_service.extract_person_embedding(best_frame.person_crop_bgr)
    if person_emb is None:
        return None
    track.best_person_embedding = person_emb
    return person_emb


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

    matches: list[Any] = []
    inserted_matches: list[Any] = []
    auto_refresh_events: list[dict[str, Any]] = []

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
        if settings.enable_tracking:
            runtime_gates = ai_pipeline.get_runtime_gates()
            detections = ai_pipeline.detect_faces(image)
            tracks = tracker_manager.update(
                camera_id=camera_id,
                detections=detections,
                frame=image,
            )
            tracker_manager.cleanup_stale(settings.tracking_max_age_seconds)

            template_banks = _build_template_banks(db)
            tracked_matches: list[_TrackMatch] = []
            for track in tracks:
                best_frame = track.best_frame
                if best_frame is None:
                    continue

                if not track.needs_identification(
                    consistent_required=settings.tracking_consistent_match_count,
                    quality_drop_ratio=settings.tracking_quality_drop_ratio,
                ):
                    if track.identity is not None:
                        if settings.enable_cross_camera_reid:
                            _ensure_track_person_embedding(track)
                        if track.status == "confirmed" and settings.enable_cross_camera_reid:
                            cross_camera_linker.register_confirmed_track(camera_id, track)

                        tracked_matches.append(
                            _TrackMatch(
                                student_id=int(track.identity),
                                confidence=float(track.confidence),
                                bbox=best_frame.bbox,
                                quality=float(best_frame.quality_score),
                                track_id=int(track.track_id),
                            )
                        )
                    continue

                identified, best_probe = _identify_track_from_best_frame(
                    track=track,
                    runtime_gates=runtime_gates,
                    template_banks=template_banks,
                )
                if identified is None:
                    continue

                track.record_identity(
                    student_id=identified.student_id,
                    confidence=identified.confidence,
                    consistent_required=settings.tracking_consistent_match_count,
                    embedding=best_probe,
                )
                tracked_matches.append(identified)

                if settings.enable_cross_camera_reid:
                    _ensure_track_person_embedding(track)
                    if track.status == "confirmed":
                        cross_camera_linker.register_confirmed_track(camera_id, track)

            if settings.enable_cross_camera_reid:
                for track in tracks:
                    if track.identity is not None:
                        continue
                    best_frame = track.best_frame
                    if best_frame is None:
                        continue

                    person_emb = _ensure_track_person_embedding(track)
                    if person_emb is None:
                        continue

                    link = cross_camera_linker.try_link_track(camera_id, track)
                    if link is None:
                        continue

                    track.identity = int(link["student_id"])
                    track.confidence = float(link["fused_score"])
                    track.force_reid = False

                    tracked_matches.append(
                        _TrackMatch(
                            student_id=int(link["student_id"]),
                            confidence=float(link["fused_score"]),
                            bbox=best_frame.bbox,
                            quality=float(best_frame.quality_score),
                            track_id=int(track.track_id),
                            cross_camera_source_track_id=int(link["source_track_id"]),
                        )
                    )

            matches = _dedupe_matches_by_student(tracked_matches)
        else:
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
            track_id = getattr(match, "track_id", None)
            cross_camera_source_track_id = getattr(
                match,
                "cross_camera_source_track_id",
                None,
            )
            db.add(
                Detection(
                    snapshot_id=snapshot.id,
                    student_id=match.student_id,
                    confidence=match.confidence,
                    camera_id=camera_id,
                    track_id=track_id,
                    cross_camera_source_track_id=cross_camera_source_track_id,
                )
            )
            existing_sightings.add(match.student_id)
            new_detections += 1
            inserted_matches.append(match)

            if settings.enable_auto_template_refresh:
                try:
                    # Snapshots have no liveness evidence — honour explicit policy flag.
                    liveness_ok = not settings.auto_refresh_require_liveness
                    refresh_event = maybe_auto_refresh_template_sync(
                        db,
                        match=match,
                        image_bgr=image,
                        liveness_passed=liveness_ok,
                        refreshed_by="system",
                    )
                    if refresh_event is not None:
                        auto_refresh_events.append(refresh_event)
                except Exception as exc:
                    logger.warning("template refresh skipped due to error: %s", exc)

        db.commit()

    # Annotate image for debug frame store
    annotated = ai_pipeline.annotate_image(image, matches)

    # Publish events to Redis for SSE/WebSocket streaming
    try:
        import redis as sync_redis
        import json

        r = sync_redis.Redis.from_url(settings.redis_url)
        for match in inserted_matches:
            r.publish(
                "attendance:detections",
                json.dumps({
                    "type": "detection",
                    "schedule_id": schedule_id,
                    "student_id": match.student_id,
                    "confidence": round(match.confidence, 4),
                    "snapshot_id": snapshot.id,
                    "camera_id": camera_id,
                    "track_id": getattr(match, "track_id", None),
                    "cross_camera_source_track_id": getattr(
                        match,
                        "cross_camera_source_track_id",
                        None,
                    ),
                }),
            )

        for event in auto_refresh_events:
            r.publish(
                "system:alerts",
                json.dumps(
                    {
                        "type": "template_auto_refresh",
                        "camera_id": camera_id,
                        "schedule_id": schedule_id,
                        **event,
                    }
                ),
            )

        if settings.enable_tracking:
            track_stats = tracker_manager.camera_diagnostics(camera_id)
            r.setex(
                f"tracking:stats:{camera_id}",
                max(60, int(settings.tracking_max_age_seconds)),
                json.dumps(track_stats),
            )

            if settings.enable_cross_camera_reid:
                r.setex(
                    "tracking:cross_camera_stats",
                    max(60, int(settings.cross_camera_time_window_seconds)),
                    json.dumps(cross_camera_linker.metrics_snapshot()),
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
        "auto_refreshed": len(auto_refresh_events),
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
    2. Run 5-tier liveness verification (tiers 4-5 are flag/capability-gated)
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

    # Recognition keeps lightweight sampling; liveness can use denser sampling for rPPG.
    recognition_indices = np.linspace(0, len(frames) - 1, min(5, len(frames)), dtype=int)
    recognition_frames = [frames[i] for i in recognition_indices]

    liveness_sample_count = min(
        len(frames),
        max(5, int(settings.rppg_min_frames) if settings.enable_rppg_liveness else 5),
    )
    liveness_indices = np.linspace(0, len(frames) - 1, liveness_sample_count, dtype=int)
    sampled_frames = [frames[i] for i in liveness_indices]

    from backend.services.camera_profiles import get_camera_profile

    camera_profile = get_camera_profile(camera_id)
    flash_supported = bool(camera_profile.get("supports_flash_liveness", False))

    face_bboxes: list[tuple[int, int, int, int] | None] = []
    face_crops: list[np.ndarray] = []
    for frame in sampled_frames:
        boxes = ai_pipeline.detect_faces(frame)
        if not boxes:
            face_bboxes.append(None)
            continue

        best_box = max(boxes, key=lambda b: int(b[2]) * int(b[3]))
        face_bboxes.append(best_box)

        x, y, w, h = best_box
        x1 = max(0, int(x))
        y1 = max(0, int(y))
        x2 = min(frame.shape[1], int(x + w))
        y2 = min(frame.shape[0], int(y + h))
        if x2 > x1 and y2 > y1:
            face_crops.append(frame[y1:y2, x1:x2])

    # Liveness check
    from backend.services.liveness import check_liveness

    flash_pair: tuple[np.ndarray, np.ndarray] | None = None
    if settings.enable_flash_liveness and flash_supported and len(sampled_frames) >= 2:
        flash_pair = (sampled_frames[0], sampled_frames[1])

    first_face_bbox = next((bbox for bbox in face_bboxes if bbox is not None), None)
    liveness_result = check_liveness(
        sampled_frames,
        face_crops=face_crops,
        face_bboxes=face_bboxes,
        motion_threshold=settings.liveness_motion_threshold,
        flow_min_magnitude=settings.liveness_flow_min_magnitude,
        spoof_threshold=settings.liveness_spoof_threshold,
        enable_rppg=settings.enable_rppg_liveness,
        rppg_min_frames=settings.rppg_min_frames,
        rppg_signal_threshold=settings.rppg_signal_threshold,
        enable_flash=settings.enable_flash_liveness,
        camera_supports_flash=flash_supported,
        flash_scattering_threshold=settings.flash_scattering_threshold,
        flash_frame_pair=flash_pair,
        flash_face_bbox=first_face_bbox,
    )

    if not liveness_result.is_live:
        return {
            "status": "liveness_failed",
            "message": liveness_result.detail,
            "tier1_score": liveness_result.tier1_motion_score,
            "tier2_magnitude": liveness_result.tier2_flow_magnitude,
            "tier3_spoof_score": liveness_result.tier3_spoof_score,
            "tier4_status": liveness_result.tier4_status,
            "tier4_signal_quality": liveness_result.tier4_rppg_signal_quality,
            "tier4_estimated_hr_bpm": liveness_result.tier4_estimated_hr_bpm,
            "tier5_status": liveness_result.tier5_status,
            "tier5_scattering_score": liveness_result.tier5_scattering_score,
            "tier5_pattern": liveness_result.tier5_pattern_analysis,
        }

    # Multi-frame recognition with voting
    with SyncSession() as db:
        from collections import Counter

        all_match_frames: list[tuple[FaceMatch, np.ndarray]] = []
        for frame in recognition_frames[::2]:  # Process every other sampled frame
            matches = ai_pipeline.recognize(
                db_session=db, image_bgr=frame, schedule_id=schedule_id
            )
            for match in matches:
                all_match_frames.append((match, frame))

        all_matches = [entry[0] for entry in all_match_frames]

        # Voting: student must appear in >= 2 frames
        student_votes: Counter[int] = Counter()
        student_best: dict[int, tuple[FaceMatch, np.ndarray]] = {}
        for match, source_frame in all_match_frames:
            student_votes[match.student_id] += 1
            prev = student_best.get(match.student_id)
            if prev is None or match.confidence > prev[0].confidence:
                student_best[match.student_id] = (match, source_frame)

        confirmed_entries = [
            student_best[sid]
            for sid, votes in student_votes.items()
            if votes >= 2 and sid in student_best
        ]
        confirmed = [entry[0] for entry in confirmed_entries]
        auto_refresh_events: list[dict[str, Any]] = []

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

        for match, source_frame in confirmed_entries:
            db.add(
                Detection(
                    snapshot_id=snapshot.id,
                    student_id=match.student_id,
                    confidence=match.confidence,
                    camera_id=camera_id,
                )
            )

            if settings.enable_auto_template_refresh:
                try:
                    refresh_event = maybe_auto_refresh_template_sync(
                        db,
                        match=match,
                        image_bgr=source_frame,
                        liveness_passed=True,
                        refreshed_by="system",
                    )
                    if refresh_event is not None:
                        auto_refresh_events.append(refresh_event)
                except Exception as exc:
                    logger.warning("clip template refresh skipped due to error: %s", exc)
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
        for event in auto_refresh_events:
            r.publish(
                "system:alerts",
                json.dumps(
                    {
                        "type": "template_auto_refresh",
                        "camera_id": camera_id,
                        "schedule_id": schedule_id,
                        **event,
                    }
                ),
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
        "auto_refreshed": len(auto_refresh_events),
        "tier4_status": liveness_result.tier4_status,
        "tier4_signal_quality": liveness_result.tier4_rppg_signal_quality,
        "tier4_estimated_hr_bpm": liveness_result.tier4_estimated_hr_bpm,
        "tier5_status": liveness_result.tier5_status,
        "tier5_scattering_score": liveness_result.tier5_scattering_score,
        "tier5_pattern": liveness_result.tier5_pattern_analysis,
    }
