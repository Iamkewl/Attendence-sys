"""Student management and enrollment routes."""

from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_db, require_role, get_current_user
from backend.core.config import get_settings
from backend.core.constants import (
    EMBEDDING_DIMENSION,
    MIN_ENROLLMENT_PHOTOS,
    PoseLabel,
    UserRole,
)
from backend.models.attendance import Detection, Snapshot
from backend.models.course import Course, Schedule
from backend.models.student import Student, StudentEmbedding
from backend.models.audit import AuditLog
from backend.models.user import User
from backend.services.ai_pipeline import ai_pipeline
from backend.services.audit_service import log_audit
from backend.services.data_retention import delete_student_biometric_data_async
from backend.schemas.student import (
    EmbeddingRead,
    EnrollmentAnalyticsRead,
    EnrollmentAnalyticsHistoryRead,
    EnrollmentTestCandidateRead,
    EnrollmentTestRead,
    EnrollFromEmbeddingRequest,
    EnrollmentQualitySummaryRead,
    EnrollmentSummaryRead,
    EnrollmentTemplateRead,
    EnrollmentTemplateStatusUpdate,
    StudentCreate,
    StudentRead,
    StudentUpdate,
)

router = APIRouter()
settings = get_settings()
_ACTIVE_LIMIT_PER_BUCKET = int(getattr(settings, "active_templates_per_bucket", 5))
_BACKUP_LIMIT_PER_BUCKET = int(getattr(settings, "backup_templates_per_bucket", 10))
_DUPLICATE_SIMILARITY_THRESHOLD = float(
    getattr(settings, "enrollment_duplicate_similarity_threshold", 0.995)
)
_COLLISION_SIMILARITY_THRESHOLD = float(
    getattr(settings, "enrollment_collision_similarity_threshold", 0.93)
)
_ENROLLMENT_MODEL_NAME = "lvface"

_REJECT_REASON_LABELS: dict[str, str] = {
    "no_face_detected": "No face detected",
    "multiple_faces_detected": "Multiple faces detected",
    "face_too_small": "Face too small",
    "image_too_blurry": "Image too blurry",
    "face_quality_low": "Face quality too low",
    "empty_file": "Empty file",
    "invalid_image_format": "Invalid image format",
    "crop_failed": "Face crop failed",
    "embedding_extraction_failed": "Embedding extraction failed",
    "invalid_embedding_vector": "Invalid embedding vector",
    "duplicate_embedding": "Duplicate embedding candidate",
    "collision_risk": "Too close to another student",
    "other": "Other rejection",
}

_REJECT_REASON_GUIDANCE: dict[str, str] = {
    "no_face_detected": "Center your face in the frame and improve front lighting before capture.",
    "multiple_faces_detected": "Only one person should be visible. Ask others to move out of frame.",
    "face_too_small": "Move closer so your face occupies more of the frame.",
    "image_too_blurry": "Hold camera steady and avoid motion during capture burst.",
    "face_quality_low": "Increase brightness and face the camera directly for a clearer capture.",
    "empty_file": "Retry capture and confirm the camera stream is active.",
    "invalid_image_format": "Use a valid JPG/PNG capture source.",
    "crop_failed": "Reposition to keep your full face inside the camera frame.",
    "embedding_extraction_failed": "Try another burst with stronger lighting and frontal pose.",
    "invalid_embedding_vector": "Retry capture with a clearer, front-facing image.",
    "duplicate_embedding": "Vary angle/expression slightly across burst frames to improve diversity.",
    "collision_risk": "Capture a sharper frontal image with neutral expression and good lighting.",
}


def _required_pose_coverage() -> dict[str, int]:
    return {
        PoseLabel.FRONTAL.value: int(
            getattr(settings, "enrollment_min_frontal_embeddings", 3)
        ),
        PoseLabel.LEFT_34.value: int(
            getattr(settings, "enrollment_min_left_34_embeddings", 1)
        ),
        PoseLabel.RIGHT_34.value: int(
            getattr(settings, "enrollment_min_right_34_embeddings", 1)
        ),
    }


def _missing_pose_coverage(pose_coverage: dict[str, int]) -> dict[str, int]:
    required = _required_pose_coverage()
    missing: dict[str, int] = {}
    for pose, needed in required.items():
        current = int(pose_coverage.get(pose, 0))
        if current < needed:
            missing[pose] = needed - current
    return missing


@router.post(
    "",
    response_model=StudentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student record",
)
async def create_student(
    body: StudentCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Create a new student record."""
    student = Student(**body.model_dump())
    db.add(student)
    await db.flush()
    await db.refresh(student)
    return student


@router.get(
    "",
    response_model=list[StudentRead],
    summary="List students",
)
async def list_students(
    skip: int = 0,
    limit: int = 50,
    enrolled_only: bool = False,
    course_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """List students with optional enrollment and observed-course filters."""
    query = select(Student)
    if enrolled_only:
        query = query.where(Student.is_enrolled == True)  # noqa: E712
    if course_id is not None:
        query = (
            query.join(Detection, Detection.student_id == Student.id)
            .join(Snapshot, Snapshot.id == Detection.snapshot_id)
            .join(Schedule, Schedule.id == Snapshot.schedule_id)
            .where(Schedule.course_id == course_id)
            .distinct()
        )

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    students = result.scalars().all()
    if not students:
        return []

    student_ids = [int(student.id) for student in students]
    course_rows = await db.execute(
        select(
            Detection.student_id,
            Course.id,
            Course.code,
            Course.name,
        )
        .join(Snapshot, Snapshot.id == Detection.snapshot_id)
        .join(Schedule, Schedule.id == Snapshot.schedule_id)
        .join(Course, Course.id == Schedule.course_id)
        .where(Detection.student_id.in_(student_ids))
        .group_by(
            Detection.student_id,
            Course.id,
            Course.code,
            Course.name,
        )
        .order_by(Detection.student_id, Course.code, Course.name)
    )

    course_map: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for sid, cid, code, name in course_rows.all():
        course_map[int(sid)].append((int(cid), f"{code} {name}".strip()))

    response: list[StudentRead] = []
    for student in students:
        dto = StudentRead.model_validate(student)
        mapped_courses = course_map.get(int(student.id), [])
        response.append(
            dto.model_copy(
                update={
                    "course_ids": [course[0] for course in mapped_courses],
                    "course_names": [course[1] for course in mapped_courses],
                    "course_count": len(mapped_courses),
                }
            )
        )

    return response


def _quality_failure_reason(
    *,
    detected_faces: int,
    face_size_px: int,
    sharpness: float,
    quality_score: float,
    runtime_gates: dict,
) -> str | None:
    if detected_faces == 0:
        return "No face detected"
    if detected_faces > 1:
        return "Multiple faces detected"
    min_face_size_px = int(runtime_gates["min_face_size_px"])
    min_blur_variance = float(runtime_gates["min_blur_variance"])
    min_face_quality_score = float(runtime_gates["min_face_quality_score"])

    if face_size_px < min_face_size_px:
        return (
            f"Face too small ({face_size_px}px). Minimum is "
            f"{min_face_size_px}px"
        )
    if sharpness < min_blur_variance:
        return (
            f"Image too blurry ({sharpness:.1f}). Minimum sharpness is "
            f"{min_blur_variance:.1f}"
        )
    if quality_score < min_face_quality_score:
        return (
            f"Face quality too low ({quality_score:.3f}). Minimum is "
            f"{min_face_quality_score:.3f}"
        )
    return None


def _bbox_iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2 = ax + aw
    ay2 = ay + ah
    bx2 = bx + bw
    by2 = by + bh

    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = float(inter_w * inter_h)
    if inter_area <= 0:
        return 0.0

    a_area = float(max(aw, 0) * max(ah, 0))
    b_area = float(max(bw, 0) * max(bh, 0))
    union = max(a_area + b_area - inter_area, 1e-8)
    return float(inter_area / union)


def _select_enrollment_face_box(
    image_shape: tuple[int, int, int],
    boxes: list[tuple[int, int, int, int]],
) -> tuple[tuple[int, int, int, int] | None, str | None]:
    """Select one enrollment face box while filtering detector artifacts.

    Enrollment expects one person in frame. Dual-pass detection can emit
    duplicate or tiny false positives, so this function deduplicates overlap
    and only auto-selects when one clearly dominant face remains.
    """
    normalized = [
        (int(x), int(y), int(w), int(h))
        for x, y, w, h in boxes
        if int(w) > 0 and int(h) > 0
    ]
    if not normalized:
        return None, None

    normalized.sort(key=lambda b: b[2] * b[3], reverse=True)

    deduped: list[tuple[int, int, int, int]] = []
    for candidate in normalized:
        if any(_bbox_iou(candidate, kept) >= 0.25 for kept in deduped):
            continue
        deduped.append(candidate)

    if len(deduped) == 1:
        return deduped[0], None

    frame_h, frame_w = image_shape[:2]
    frame_area = float(max(frame_h * frame_w, 1))

    largest = deduped[0]
    largest_area = float(largest[2] * largest[3])
    second_area = float(deduped[1][2] * deduped[1][3])
    dominance_ratio = largest_area / max(second_area, 1.0)
    largest_ratio = largest_area / frame_area

    tiny_secondary = all(
        (float(box[2] * box[3]) / max(largest_area, 1.0)) <= 0.22
        or (float(box[2] * box[3]) / frame_area) <= 0.006
        for box in deduped[1:]
    )

    if (
        largest_ratio >= float(settings.min_face_area_ratio)
        and dominance_ratio >= 2.8
        and tiny_secondary
    ):
        return (
            largest,
            "Ignored tiny secondary face detections; using dominant face.",
        )

    return None, None


def _clip_unit(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _reject_reason_code(reason: str | None) -> str | None:
    if not reason:
        return None
    text = reason.lower().strip()
    if "no face detected" in text:
        return "no_face_detected"
    if "multiple faces detected" in text:
        return "multiple_faces_detected"
    if "face too small" in text:
        return "face_too_small"
    if "image too blurry" in text:
        return "image_too_blurry"
    if "face quality too low" in text:
        return "face_quality_low"
    if "empty file" in text:
        return "empty_file"
    if "invalid image format" in text:
        return "invalid_image_format"
    if "could not crop" in text:
        return "crop_failed"
    if "embedding extraction failed" in text:
        return "embedding_extraction_failed"
    if "invalid embedding vector" in text:
        return "invalid_embedding_vector"
    if "duplicate embedding candidate" in text:
        return "duplicate_embedding"
    if "embedding too close to another student" in text:
        return "collision_risk"
    return "other"


def _build_reject_diagnostics(
    checks: list[dict],
) -> tuple[dict[str, int], str | None, str | None, list[str]]:
    grouped: dict[str, int] = defaultdict(int)

    for check in checks:
        if bool(check.get("accepted")):
            continue
        code = str(check.get("reject_reason_code") or "").strip()
        if not code:
            code = _reject_reason_code(check.get("reason")) or "other"
            check["reject_reason_code"] = code
        grouped[code] += 1

    grouped_dict = dict(sorted(grouped.items(), key=lambda item: (-item[1], item[0])))
    if not grouped_dict:
        return {}, None, None, []

    dominant_code = next(iter(grouped_dict.keys()))
    dominant_label = _REJECT_REASON_LABELS.get(dominant_code, "Other rejection")

    guidance = [
        _REJECT_REASON_GUIDANCE[code]
        for code in list(grouped_dict.keys())[:2]
        if code in _REJECT_REASON_GUIDANCE
    ]
    if not guidance:
        guidance = [
            "Keep one face centered, well-lit, and stable during guided burst capture."
        ]

    return grouped_dict, dominant_code, dominant_label, guidance


def _compute_retention_score(
    *,
    quality_score: float,
    sharpness: float,
    face_size_px: int,
    novelty_score: float,
    collision_risk: float,
    runtime_gates: dict,
) -> float:
    sharpness_score = _clip_unit(
        sharpness / max(float(runtime_gates["min_blur_variance"]) * 2.0, 1e-6)
    )
    size_score = _clip_unit(
        face_size_px / max(float(runtime_gates["min_face_size_px"]) * 2.0, 1.0)
    )
    stability_score = _clip_unit(1.0 - collision_risk)
    return float(
        0.35 * _clip_unit(quality_score)
        + 0.20 * sharpness_score
        + 0.20 * size_score
        + 0.15 * _clip_unit(novelty_score)
        + 0.10 * stability_score
    )


async def _compute_novelty(
    db: AsyncSession,
    *,
    student_id: int,
    model_name: str,
    embedding: list[float],
) -> tuple[float, float]:
    import numpy as np

    result = await db.execute(
        select(StudentEmbedding.embedding).where(
            StudentEmbedding.student_id == student_id,
            StudentEmbedding.model_name == model_name,
        )
    )
    existing = result.scalars().all()
    if not existing:
        return 1.0, 0.0

    probe = np.asarray(embedding, dtype=np.float32)
    probe /= max(float(np.linalg.norm(probe)), 1e-8)
    max_similarity = -1.0
    for raw in existing:
        candidate = np.asarray(raw, dtype=np.float32)
        candidate /= max(float(np.linalg.norm(candidate)), 1e-8)
        sim = float(np.dot(probe, candidate))
        if sim > max_similarity:
            max_similarity = sim

    max_similarity = max(0.0, max_similarity)
    novelty = _clip_unit(1.0 - max_similarity)
    return novelty, max_similarity


async def _compute_collision_risk(
    db: AsyncSession,
    *,
    student_id: int,
    model_name: str,
    embedding: list[float],
) -> float:
    import numpy as np

    result = await db.execute(
        select(StudentEmbedding.embedding).where(
            StudentEmbedding.student_id != student_id,
            StudentEmbedding.model_name == model_name,
            StudentEmbedding.template_status != "quarantined",
        )
    )
    foreign = result.scalars().all()
    if not foreign:
        return 0.0

    probe = np.asarray(embedding, dtype=np.float32)
    probe /= max(float(np.linalg.norm(probe)), 1e-8)
    max_similarity = -1.0
    for raw in foreign:
        candidate = np.asarray(raw, dtype=np.float32)
        candidate /= max(float(np.linalg.norm(candidate)), 1e-8)
        sim = float(np.dot(probe, candidate))
        if sim > max_similarity:
            max_similarity = sim

    return _clip_unit(max(0.0, max_similarity))


async def _rebalance_student_templates(db: AsyncSession, student_id: int) -> None:
    result = await db.execute(
        select(StudentEmbedding).where(StudentEmbedding.student_id == student_id)
    )
    all_embeddings = result.scalars().all()
    buckets: dict[tuple[str, str, str], list[StudentEmbedding]] = defaultdict(list)
    for row in all_embeddings:
        buckets[(row.model_name, row.pose_label, row.resolution)].append(row)

    for rows in buckets.values():
        rows.sort(key=lambda item: float(item.retention_score or 0.0), reverse=True)
        for idx, row in enumerate(rows):
            if idx < _ACTIVE_LIMIT_PER_BUCKET:
                row.template_status = "active"
                row.is_active = True
            elif idx < (_ACTIVE_LIMIT_PER_BUCKET + _BACKUP_LIMIT_PER_BUCKET):
                row.template_status = "backup"
                row.is_active = False
            else:
                row.template_status = "quarantined"
                row.is_active = False


async def _build_quality_summary(
    db: AsyncSession, student: Student
) -> EnrollmentQualitySummaryRead:
    result = await db.execute(
        select(StudentEmbedding).where(StudentEmbedding.student_id == student.id)
    )
    rows = result.scalars().all()

    pose_coverage: dict[str, int] = defaultdict(int)
    bucket_rows: dict[tuple[str, str, str], list[StudentEmbedding]] = defaultdict(list)
    for row in rows:
        bucket_rows[(row.pose_label, row.resolution, row.model_name)].append(row)
        if (
            row.model_name == _ENROLLMENT_MODEL_NAME
            and row.is_active
            and row.template_status == "active"
        ):
            pose_coverage[row.pose_label] += 1

    buckets = []
    for (pose_label, resolution, model_name), items in sorted(bucket_rows.items()):
        active_count = sum(1 for i in items if i.template_status == "active")
        backup_count = sum(1 for i in items if i.template_status == "backup")
        quarantined_count = sum(
            1 for i in items if i.template_status == "quarantined"
        )

        quality_values = [
            float(i.capture_quality_score)
            for i in items
            if i.capture_quality_score is not None
        ]
        retention_values = [
            float(i.retention_score) for i in items if i.retention_score is not None
        ]
        average_quality = (
            float(sum(quality_values) / len(quality_values))
            if quality_values
            else None
        )
        average_retention = (
            float(sum(retention_values) / len(retention_values))
            if retention_values
            else None
        )
        buckets.append(
            {
                "pose_label": pose_label,
                "resolution": resolution,
                "model_name": model_name,
                "active_count": active_count,
                "backup_count": backup_count,
                "quarantined_count": quarantined_count,
                "average_quality_score": average_quality,
                "average_retention_score": average_retention,
            }
        )

    active_embeddings = sum(
        1
        for row in rows
        if row.model_name == _ENROLLMENT_MODEL_NAME
        and row.template_status == "active"
        and row.is_active
    )
    total_embeddings = sum(
        1
        for row in rows
        if row.model_name == _ENROLLMENT_MODEL_NAME
        and row.template_status != "quarantined"
    )

    missing_pose_coverage = _missing_pose_coverage(dict(pose_coverage))

    return EnrollmentQualitySummaryRead(
        student_id=student.id,
        enrolled=student.is_enrolled,
        required_embeddings=MIN_ENROLLMENT_PHOTOS,
        required_pose_coverage=_required_pose_coverage(),
        active_embeddings=active_embeddings,
        total_embeddings=total_embeddings,
        pose_coverage=dict(pose_coverage),
        missing_pose_coverage=missing_pose_coverage,
        buckets=buckets,
    )


@router.post(
    "/{student_id}/enroll/images",
    response_model=EnrollmentSummaryRead,
    summary="Enroll student from face images with quality checks",
)
async def enroll_from_images(
    student_id: int,
    images: list[UploadFile] = File(...),
    pose_label: str = Form("frontal"),
    auto_pose: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    actor_user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Enroll student from uploaded images and validate embedding quality."""
    import cv2
    import numpy as np

    if not images:
        raise HTTPException(status_code=400, detail="No images were uploaded")
    if len(images) > 30:
        raise HTTPException(status_code=400, detail="Too many images (max 30)")
    if pose_label not in {
        PoseLabel.FRONTAL.value,
        PoseLabel.LEFT_34.value,
        PoseLabel.RIGHT_34.value,
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid pose label. Use one of: "
                "frontal, left_34, right_34"
            ),
        )

    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    runtime_gates = ai_pipeline.get_runtime_gates()

    checks: list[dict] = []
    accepted = 0

    for uploaded in images:
        image_name = uploaded.filename or "upload"
        raw = await uploaded.read()
        if not raw:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": "Empty file",
                    "reject_reason_code": "empty_file",
                    "detected_faces": 0,
                }
            )
            continue

        decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": "Invalid image format",
                    "reject_reason_code": "invalid_image_format",
                    "detected_faces": 0,
                }
            )
            continue

        boxes = ai_pipeline.detect_faces_sahi(decoded)
        detected_faces = len(boxes)
        selected_box, face_selection_warning = _select_enrollment_face_box(
            decoded.shape, boxes
        )
        if selected_box is None:
            reason = _quality_failure_reason(
                detected_faces=detected_faces,
                face_size_px=0,
                sharpness=0.0,
                quality_score=0.0,
                runtime_gates=runtime_gates,
            ) or "Could not isolate one face for enrollment."
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": reason,
                    "reject_reason_code": _reject_reason_code(reason),
                    "detected_faces": detected_faces,
                }
            )
            continue

        x, y, w, h = selected_box
        x1 = max(x, 0)
        y1 = max(y, 0)
        x2 = min(x + w, decoded.shape[1])
        y2 = min(y + h, decoded.shape[0])
        crop = decoded[y1:y2, x1:x2]
        if crop.size == 0:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": "Could not crop detected face",
                    "reject_reason_code": "crop_failed",
                    "detected_faces": detected_faces,
                    "face_size_px": 0,
                    "face_selection_warning": face_selection_warning,
                }
            )
            continue

        quality_score, sharpness = ai_pipeline.face_quality_score(
            crop_bgr=crop,
            bbox=(x, y, w, h),
            full_image_shape=decoded.shape,
            runtime_gates=runtime_gates,
        )
        estimated_pose_label, pose_confidence = ai_pipeline.estimate_pose_label(crop)
        pose_confidence = float(pose_confidence)
        pose_label_used = pose_label
        pose_warning: str | None = None
        if auto_pose and pose_confidence >= 0.55:
            pose_label_used = estimated_pose_label
        elif (not auto_pose) and pose_confidence >= 0.7 and estimated_pose_label != pose_label:
            pose_warning = (
                f"Estimated pose is {estimated_pose_label}. "
                "Consider using auto-pose or a matching pose label."
            )

        face_size_px = min(w, h)
        area_ratio = float((w * h) / max(decoded.shape[0] * decoded.shape[1], 1))

        reason = _quality_failure_reason(
            detected_faces=1,
            face_size_px=face_size_px,
            sharpness=sharpness,
            quality_score=quality_score,
            runtime_gates=runtime_gates,
        )
        if reason:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": reason,
                    "reject_reason_code": _reject_reason_code(reason),
                    "detected_faces": detected_faces,
                    "face_size_px": face_size_px,
                    "area_ratio": area_ratio,
                    "sharpness": sharpness,
                    "quality_score": quality_score,
                    "estimated_pose_label": estimated_pose_label,
                    "pose_confidence": pose_confidence,
                    "pose_label_used": pose_label_used,
                    "pose_warning": pose_warning,
                    "face_selection_warning": face_selection_warning,
                }
            )
            continue

        emb = ai_pipeline.extract_embedding_lvface(crop)
        if emb is None:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": "Embedding extraction failed",
                    "reject_reason_code": "embedding_extraction_failed",
                    "detected_faces": detected_faces,
                    "face_size_px": face_size_px,
                    "area_ratio": area_ratio,
                    "sharpness": sharpness,
                    "quality_score": quality_score,
                    "estimated_pose_label": estimated_pose_label,
                    "pose_confidence": pose_confidence,
                    "pose_label_used": pose_label_used,
                    "pose_warning": pose_warning,
                    "face_selection_warning": face_selection_warning,
                }
            )
            continue

        emb = np.asarray(emb, dtype=np.float32).flatten()
        if emb.shape[0] != EMBEDDING_DIMENSION or not np.isfinite(emb).all():
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": "Invalid embedding vector",
                    "reject_reason_code": "invalid_embedding_vector",
                    "detected_faces": detected_faces,
                    "face_size_px": face_size_px,
                    "area_ratio": area_ratio,
                    "sharpness": sharpness,
                    "quality_score": quality_score,
                    "face_selection_warning": face_selection_warning,
                }
            )
            continue

        novelty_score, max_same_student_similarity = await _compute_novelty(
            db,
            student_id=student_id,
            model_name=_ENROLLMENT_MODEL_NAME,
            embedding=emb.tolist(),
        )
        if max_same_student_similarity >= _DUPLICATE_SIMILARITY_THRESHOLD:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": (
                        "Duplicate embedding candidate. Capture a different "
                        "angle or expression."
                    ),
                    "reject_reason_code": "duplicate_embedding",
                    "detected_faces": 1,
                    "face_size_px": face_size_px,
                    "area_ratio": area_ratio,
                    "sharpness": sharpness,
                    "quality_score": quality_score,
                    "novelty_score": novelty_score,
                    "estimated_pose_label": estimated_pose_label,
                    "pose_confidence": pose_confidence,
                    "pose_label_used": pose_label_used,
                    "pose_warning": pose_warning,
                    "face_selection_warning": face_selection_warning,
                }
            )
            continue

        collision_risk = await _compute_collision_risk(
            db,
            student_id=student_id,
            model_name=_ENROLLMENT_MODEL_NAME,
            embedding=emb.tolist(),
        )
        if collision_risk >= _COLLISION_SIMILARITY_THRESHOLD:
            checks.append(
                {
                    "filename": image_name,
                    "accepted": False,
                    "reason": (
                        "Embedding too close to another student template. "
                        "Capture a clearer frontal image."
                    ),
                    "reject_reason_code": "collision_risk",
                    "detected_faces": 1,
                    "face_size_px": face_size_px,
                    "area_ratio": area_ratio,
                    "sharpness": sharpness,
                    "quality_score": quality_score,
                    "novelty_score": novelty_score,
                    "collision_risk": collision_risk,
                    "estimated_pose_label": estimated_pose_label,
                    "pose_confidence": pose_confidence,
                    "pose_label_used": pose_label_used,
                    "pose_warning": pose_warning,
                    "face_selection_warning": face_selection_warning,
                }
            )
            continue

        embedding_norm = float(np.linalg.norm(emb))
        retention_score = _compute_retention_score(
            quality_score=float(quality_score),
            sharpness=float(sharpness),
            face_size_px=int(face_size_px),
            novelty_score=float(novelty_score),
            collision_risk=float(collision_risk),
            runtime_gates=runtime_gates,
        )
        resolution = "low_res" if face_size_px < 96 else "full"
        db.add(
            StudentEmbedding(
                student_id=student_id,
                pose_label=pose_label_used,
                resolution=resolution,
                model_name=_ENROLLMENT_MODEL_NAME,
                embedding=emb.tolist(),
                capture_quality_score=float(quality_score),
                sharpness=float(sharpness),
                face_size_px=int(face_size_px),
                face_area_ratio=float(area_ratio),
                embedding_norm=embedding_norm,
                novelty_score=float(novelty_score),
                collision_risk=float(collision_risk),
                retention_score=float(retention_score),
                template_status="backup",
                is_active=False,
            )
        )
        accepted += 1

        checks.append(
            {
                "filename": image_name,
                "accepted": True,
                "detected_faces": detected_faces,
                "face_size_px": face_size_px,
                "area_ratio": area_ratio,
                "sharpness": sharpness,
                "quality_score": quality_score,
                "embedding_norm": embedding_norm,
                "novelty_score": novelty_score,
                "collision_risk": collision_risk,
                "retention_score": retention_score,
                "template_status": "candidate",
                "estimated_pose_label": estimated_pose_label,
                "pose_confidence": pose_confidence,
                "pose_label_used": pose_label_used,
                "pose_warning": pose_warning,
                "face_selection_warning": face_selection_warning,
            }
        )

    await db.flush()
    await _rebalance_student_templates(db, student_id)
    await db.flush()

    active_result = await db.execute(
        select(func.count(StudentEmbedding.id)).where(
            StudentEmbedding.student_id == student_id,
            StudentEmbedding.model_name == _ENROLLMENT_MODEL_NAME,
            StudentEmbedding.template_status == "active",
            StudentEmbedding.is_active.is_(True),
        )
    )
    active_embeddings = int(active_result.scalar_one() or 0)

    total_result = await db.execute(
        select(func.count(StudentEmbedding.id)).where(
            StudentEmbedding.student_id == student_id,
            StudentEmbedding.model_name == _ENROLLMENT_MODEL_NAME,
            StudentEmbedding.template_status != "quarantined",
        )
    )
    total_embeddings = int(total_result.scalar_one() or 0)

    pose_result = await db.execute(
        select(StudentEmbedding.pose_label, func.count(StudentEmbedding.id))
        .where(
            StudentEmbedding.student_id == student_id,
            StudentEmbedding.model_name == _ENROLLMENT_MODEL_NAME,
            StudentEmbedding.template_status == "active",
            StudentEmbedding.is_active.is_(True),
        )
        .group_by(StudentEmbedding.pose_label)
    )
    pose_coverage = {
        str(row[0]): int(row[1])
        for row in pose_result.all()
    }
    missing_pose_coverage = _missing_pose_coverage(pose_coverage)

    student.is_enrolled = active_embeddings >= MIN_ENROLLMENT_PHOTOS and not missing_pose_coverage

    await db.flush()
    await db.refresh(student)

    remaining = max(MIN_ENROLLMENT_PHOTOS - active_embeddings, 0)
    if student.is_enrolled:
        message = (
            f"Enrollment complete. Accepted {accepted}/{len(images)} images. "
            f"Student is now enrolled."
        )
    else:
        pieces = [f"Accepted {accepted}/{len(images)} images."]
        if remaining > 0:
            pieces.append(
                f"Need {remaining} more active quality template(s)."
            )
        if missing_pose_coverage:
            pose_hint = ", ".join(
                f"{pose}:{count}"
                for pose, count in sorted(missing_pose_coverage.items())
            )
            pieces.append(f"Missing pose coverage -> {pose_hint}.")
        pieces.append("Upload side profiles to complete enrollment.")
        message = " ".join(pieces)

    (
        reject_reason_groups,
        dominant_reject_reason_code,
        dominant_reject_reason_label,
        capture_guidance,
    ) = _build_reject_diagnostics(checks)

    await log_audit(
        db,
        user_id=actor_user.id,
        action="student.enroll.images",
        resource="student_embeddings",
        details={
            "student_id": student_id,
            "accepted": accepted,
            "uploaded": len(images),
            "active_embeddings": active_embeddings,
            "total_embeddings": total_embeddings,
            "pose_coverage": pose_coverage,
            "missing_pose_coverage": missing_pose_coverage,
            "reject_reason_groups": reject_reason_groups,
            "dominant_reject_reason_code": dominant_reject_reason_code,
            "auto_pose": auto_pose,
            "model_name": _ENROLLMENT_MODEL_NAME,
        },
    )

    return EnrollmentSummaryRead(
        student_id=student.id,
        required_embeddings=MIN_ENROLLMENT_PHOTOS,
        total_embeddings=total_embeddings,
        new_embeddings=accepted,
        enrolled=student.is_enrolled,
        pose_coverage=pose_coverage,
        missing_pose_coverage=missing_pose_coverage,
        checks=checks,
        reject_reason_groups=reject_reason_groups,
        dominant_reject_reason_code=dominant_reject_reason_code,
        dominant_reject_reason_label=dominant_reject_reason_label,
        capture_guidance=capture_guidance,
        message=message,
    )


@router.get(
    "/{student_id}/enrollment/quality",
    response_model=EnrollmentQualitySummaryRead,
    summary="Get enrollment quality summary for a student",
)
async def get_enrollment_quality(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return quality and template lifecycle summary for a student's enrollment."""
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return await _build_quality_summary(db, student)


@router.get(
    "/{student_id}/enrollment/templates",
    response_model=list[EnrollmentTemplateRead],
    summary="List enrollment templates for a student",
)
async def list_enrollment_templates(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """List template lifecycle and quality details for one student."""
    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    result = await db.execute(
        select(StudentEmbedding)
        .where(StudentEmbedding.student_id == student_id)
        .order_by(
            StudentEmbedding.model_name,
            StudentEmbedding.pose_label,
            StudentEmbedding.resolution,
            StudentEmbedding.retention_score.desc(),
        )
    )
    return result.scalars().all()


@router.patch(
    "/{student_id}/enrollment/templates/{embedding_id}",
    response_model=EnrollmentTemplateRead,
    summary="Update enrollment template status",
)
async def update_enrollment_template_status(
    student_id: int,
    embedding_id: int,
    body: EnrollmentTemplateStatusUpdate,
    db: AsyncSession = Depends(get_db),
    actor_user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Promote, demote, or quarantine a specific template."""
    result = await db.execute(
        select(StudentEmbedding).where(
            StudentEmbedding.id == embedding_id,
            StudentEmbedding.student_id == student_id,
        )
    )
    embedding = result.scalar_one_or_none()
    if not embedding:
        raise HTTPException(status_code=404, detail="Enrollment template not found")

    previous_status = embedding.template_status
    embedding.template_status = body.template_status
    embedding.is_active = body.template_status == "active"

    await db.flush()
    await db.refresh(embedding)

    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    if student:
        quality = await _build_quality_summary(db, student)
        student.is_enrolled = (
            quality.active_embeddings >= MIN_ENROLLMENT_PHOTOS
            and not quality.missing_pose_coverage
        )
        await db.flush()

    await log_audit(
        db,
        user_id=actor_user.id,
        action="student.template.status.update",
        resource="student_embeddings",
        details={
            "student_id": student_id,
            "embedding_id": embedding_id,
            "from_status": previous_status,
            "to_status": body.template_status,
            "is_active": embedding.is_active,
        },
    )

    return embedding


@router.get(
    "/{student_id}/enrollment/analytics",
    response_model=EnrollmentAnalyticsRead,
    summary="Get enrollment analytics for a student",
)
async def get_enrollment_analytics(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return aggregate enrollment quality metrics for one student."""
    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    rows_result = await db.execute(
        select(StudentEmbedding).where(StudentEmbedding.student_id == student_id)
    )
    rows = rows_result.scalars().all()

    total_templates = len(rows)
    active_templates = sum(1 for row in rows if row.template_status == "active")
    backup_templates = sum(1 for row in rows if row.template_status == "backup")
    quarantined_templates = sum(
        1 for row in rows if row.template_status == "quarantined"
    )

    high_collision_templates = sum(
        1 for row in rows if float(row.collision_risk or 0.0) >= 0.85
    )
    low_quality_templates = sum(
        1
        for row in rows
        if row.capture_quality_score is not None
        and float(row.capture_quality_score) < settings.min_face_quality_score
    )

    quality_values = [
        float(row.capture_quality_score)
        for row in rows
        if row.capture_quality_score is not None
    ]
    retention_values = [
        float(row.retention_score)
        for row in rows
        if row.retention_score is not None
    ]

    quality_by_pose_values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.capture_quality_score is None:
            continue
        quality_by_pose_values[row.pose_label].append(float(row.capture_quality_score))

    quality_by_pose = {
        pose: float(sum(values) / len(values))
        for pose, values in quality_by_pose_values.items()
        if values
    }

    return EnrollmentAnalyticsRead(
        student_id=student_id,
        total_templates=total_templates,
        active_templates=active_templates,
        backup_templates=backup_templates,
        quarantined_templates=quarantined_templates,
        high_collision_templates=high_collision_templates,
        low_quality_templates=low_quality_templates,
        average_quality_score=(
            float(sum(quality_values) / len(quality_values))
            if quality_values
            else None
        ),
        average_retention_score=(
            float(sum(retention_values) / len(retention_values))
            if retention_values
            else None
        ),
        quality_by_pose=quality_by_pose,
    )


@router.get(
    "/{student_id}/enrollment/analytics/history",
    response_model=EnrollmentAnalyticsHistoryRead,
    summary="Get historical enrollment analytics for a student",
)
async def get_enrollment_analytics_history(
    student_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Return enrollment event timeline and per-pose drift history."""
    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    rows_result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.resource == "student_embeddings",
            AuditLog.action.in_([
                "student.enroll.images",
                "student.template.status.update",
            ]),
        )
        .order_by(AuditLog.created_at.asc())
        .limit(max(1, min(limit, 500)))
    )
    rows = rows_result.scalars().all()

    events = []
    pose_drift = []
    last_pose = {"frontal": 0, "left_34": 0, "right_34": 0}

    for row in rows:
        details = row.details or {}
        if int(details.get("student_id") or -1) != student_id:
            continue

        pose_coverage = details.get("pose_coverage") or {}
        missing_pose_coverage = details.get("missing_pose_coverage") or {}

        if pose_coverage:
            last_pose = {
                "frontal": int(pose_coverage.get("frontal", 0) or 0),
                "left_34": int(pose_coverage.get("left_34", 0) or 0),
                "right_34": int(pose_coverage.get("right_34", 0) or 0),
            }

        event = {
            "timestamp": row.created_at.isoformat() if row.created_at else "",
            "event_type": row.action,
            "accepted": details.get("accepted"),
            "uploaded": details.get("uploaded"),
            "active_embeddings": details.get("active_embeddings"),
            "total_embeddings": details.get("total_embeddings"),
            "pose_coverage": {
                "frontal": int(pose_coverage.get("frontal", 0) or 0),
                "left_34": int(pose_coverage.get("left_34", 0) or 0),
                "right_34": int(pose_coverage.get("right_34", 0) or 0),
            }
            if pose_coverage
            else {},
            "missing_pose_coverage": {
                k: int(v) for k, v in (missing_pose_coverage.items() if isinstance(missing_pose_coverage, dict) else [])
            },
        }
        events.append(event)

        pose_drift.append(
            {
                "timestamp": event["timestamp"],
                "frontal": int(last_pose.get("frontal", 0)),
                "left_34": int(last_pose.get("left_34", 0)),
                "right_34": int(last_pose.get("right_34", 0)),
            }
        )

    return EnrollmentAnalyticsHistoryRead(
        student_id=student_id,
        events=events,
        pose_drift_timeline=pose_drift,
    )


@router.post(
    "/{student_id}/enrollment/test",
    response_model=EnrollmentTestRead,
    summary="Run post-enrollment verification for a student",
)
async def test_enrollment_match(
    student_id: int,
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    actor_user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Test whether a probe image matches the expected enrolled student."""
    import cv2
    import numpy as np

    runtime_gates = ai_pipeline.get_runtime_gates()
    strict_threshold = float(runtime_gates["face_match_threshold"])
    relaxed_threshold = float(runtime_gates["face_match_relaxed_threshold"])
    required_margin = float(runtime_gates["face_match_margin"])

    def _failure(
        reason: str,
        *,
        detected_faces: int = 0,
        face_size_px: int | None = None,
        quality_score: float | None = None,
        sharpness: float | None = None,
        estimated_pose_label: str | None = None,
        pose_confidence: float | None = None,
        face_selection_warning: str | None = None,
    ) -> EnrollmentTestRead:
        return EnrollmentTestRead(
            student_id=student_id,
            is_match=False,
            reason=reason,
            detected_faces=detected_faces,
            face_size_px=face_size_px,
            quality_score=quality_score,
            sharpness=sharpness,
            estimated_pose_label=estimated_pose_label,
            pose_confidence=pose_confidence,
            face_selection_warning=face_selection_warning,
            strict_threshold=strict_threshold,
            relaxed_threshold=relaxed_threshold,
            required_margin=required_margin,
            candidates=[],
        )

    student_result = await db.execute(select(Student).where(Student.id == student_id))
    student = student_result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    raw = await image.read()
    if not raw:
        return _failure("Test image is empty.")

    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        return _failure("Invalid test image format.")

    boxes = ai_pipeline.detect_faces_sahi(decoded)
    detected_faces = len(boxes)
    selected_box, face_selection_warning = _select_enrollment_face_box(
        decoded.shape, boxes
    )
    if selected_box is None:
        reason = _quality_failure_reason(
            detected_faces=detected_faces,
            face_size_px=0,
            sharpness=0.0,
            quality_score=0.0,
            runtime_gates=runtime_gates,
        ) or "Could not isolate one face for testing."
        return _failure(
            reason,
            detected_faces=detected_faces,
            face_selection_warning=face_selection_warning,
        )

    x, y, w, h = selected_box
    x1 = max(x, 0)
    y1 = max(y, 0)
    x2 = min(x + w, decoded.shape[1])
    y2 = min(y + h, decoded.shape[0])
    crop = decoded[y1:y2, x1:x2]
    if crop.size == 0:
        return _failure(
            "Could not crop detected face for testing.",
            detected_faces=detected_faces,
            face_selection_warning=face_selection_warning,
        )

    quality_score, sharpness = ai_pipeline.face_quality_score(
        crop_bgr=crop,
        bbox=(x, y, w, h),
        full_image_shape=decoded.shape,
        runtime_gates=runtime_gates,
    )
    estimated_pose_label, pose_confidence = ai_pipeline.estimate_pose_label(crop)
    pose_confidence = float(pose_confidence)

    lvface_emb = ai_pipeline.extract_embedding_lvface(crop)
    if lvface_emb is None:
        return _failure(
            "Embedding extraction failed for test image.",
            detected_faces=detected_faces,
            face_size_px=int(min(w, h)),
            quality_score=float(quality_score),
            sharpness=float(sharpness),
            estimated_pose_label=estimated_pose_label,
            pose_confidence=pose_confidence,
            face_selection_warning=face_selection_warning,
        )

    lvface_probe = np.asarray(lvface_emb, dtype=np.float32).flatten()
    if lvface_probe.shape[0] != EMBEDDING_DIMENSION or not np.isfinite(lvface_probe).all():
        return _failure(
            "Invalid embedding vector generated from test image.",
            detected_faces=detected_faces,
            face_size_px=int(min(w, h)),
            quality_score=float(quality_score),
            sharpness=float(sharpness),
            estimated_pose_label=estimated_pose_label,
            pose_confidence=pose_confidence,
            face_selection_warning=face_selection_warning,
        )
    lvface_probe /= max(float(np.linalg.norm(lvface_probe)), 1e-8)

    template_rows_result = await db.execute(
        select(
            Student.id,
            Student.name,
            StudentEmbedding.embedding,
            StudentEmbedding.is_active,
            StudentEmbedding.template_status,
            StudentEmbedding.model_name,
        )
        .join(StudentEmbedding, StudentEmbedding.student_id == Student.id)
        .where(
            StudentEmbedding.model_name == _ENROLLMENT_MODEL_NAME,
            StudentEmbedding.template_status != "quarantined",
            or_(
                Student.is_enrolled.is_(True),
                Student.id == student_id,
            ),
        )
    )
    template_rows = template_rows_result.all()

    if not template_rows:
        return _failure(
            "No enrollment templates are available to run a verification test.",
            detected_faces=detected_faces,
            face_size_px=int(min(w, h)),
            quality_score=float(quality_score),
            sharpness=float(sharpness),
            estimated_pose_label=estimated_pose_label,
            pose_confidence=pose_confidence,
            face_selection_warning=face_selection_warning,
        )

    grouped: dict[int, dict[str, object]] = {}
    for sid, name, raw_embedding, is_active, template_status, model_name in template_rows:
        bucket = grouped.setdefault(
            int(sid),
            {"name": str(name), "rows": []},
        )
        row_list = bucket["rows"]
        if isinstance(row_list, list):
            row_list.append(
                {
                    "embedding": raw_embedding,
                    "is_active": bool(is_active),
                    "template_status": str(template_status),
                    "model_name": str(model_name),
                }
            )

    student_scores: dict[int, float] = {}
    student_names: dict[int, str] = {}

    for sid, bundle in grouped.items():
        rows = bundle.get("rows", [])
        if not isinstance(rows, list) or not rows:
            continue

        active_rows = [
            row
            for row in rows
            if row.get("is_active") and row.get("template_status") == "active"
        ]
        selected_rows = active_rows if active_rows else rows

        best_model_score = -1.0
        for row in selected_rows:
            candidate = np.asarray(row.get("embedding"), dtype=np.float32).flatten()
            if (
                candidate.shape[0] != EMBEDDING_DIMENSION
                or not np.isfinite(candidate).all()
            ):
                continue
            candidate /= max(float(np.linalg.norm(candidate)), 1e-8)
            score = float(np.dot(lvface_probe, candidate))
            if score > best_model_score:
                best_model_score = score

        if best_model_score < 0.0:
            continue

        student_scores[sid] = float(best_model_score)
        student_names[sid] = str(bundle.get("name") or f"Student {sid}")

    if not student_scores:
        return _failure(
            "No valid enrollment templates are available to score this test.",
            detected_faces=detected_faces,
            face_size_px=int(min(w, h)),
            quality_score=float(quality_score),
            sharpness=float(sharpness),
            estimated_pose_label=estimated_pose_label,
            pose_confidence=pose_confidence,
            face_selection_warning=face_selection_warning,
        )

    ranked = sorted(
        (
            (sid, student_names.get(sid, f"Student {sid}"), float(score))
            for sid, score in student_scores.items()
        ),
        key=lambda item: item[2],
        reverse=True,
    )

    best_sid, best_name, best_score = ranked[0]
    second_best_score = float(ranked[1][2]) if len(ranked) > 1 else 0.0
    margin = float(best_score - second_best_score)
    expected_score = student_scores.get(student_id)

    is_match = False
    reason = "Probe image does not match expected student."
    if expected_score is None:
        reason = "Expected student has no active/backup templates to verify yet."
    elif best_sid != student_id:
        reason = (
            f"Best match is {best_name} (ID {best_sid}), not the expected student."
        )
    elif best_score >= strict_threshold:
        is_match = True
        reason = "Match confirmed at strict confidence threshold."
    elif best_score >= relaxed_threshold and margin >= required_margin:
        is_match = True
        reason = "Match confirmed at relaxed threshold with sufficient margin."
    elif best_score >= relaxed_threshold:
        reason = "Similarity is close, but margin to next candidate is too small."
    else:
        reason = "Similarity is below the required matching threshold."

    candidates = [
        EnrollmentTestCandidateRead(
            student_id=int(sid),
            student_name=str(name),
            score=float(score),
        )
        for sid, name, score in ranked[:3]
    ]

    await log_audit(
        db,
        user_id=actor_user.id,
        action="student.enrollment.test",
        resource="student_embeddings",
        details={
            "student_id": student_id,
            "is_match": is_match,
            "best_match_student_id": int(best_sid),
            "best_match_score": float(best_score),
            "second_best_score": float(second_best_score),
            "margin": float(margin),
            "detected_faces": int(detected_faces),
            "face_selection_warning": face_selection_warning,
        },
    )

    return EnrollmentTestRead(
        student_id=student_id,
        is_match=is_match,
        reason=reason,
        detected_faces=detected_faces,
        face_size_px=int(min(w, h)),
        quality_score=float(quality_score),
        sharpness=float(sharpness),
        estimated_pose_label=estimated_pose_label,
        pose_confidence=pose_confidence,
        face_selection_warning=face_selection_warning,
        expected_student_score=(float(expected_score) if expected_score is not None else None),
        best_match_student_id=int(best_sid),
        best_match_student_name=best_name,
        best_match_score=float(best_score),
        second_best_score=float(second_best_score),
        margin=float(margin),
        strict_threshold=strict_threshold,
        relaxed_threshold=relaxed_threshold,
        required_margin=required_margin,
        candidates=candidates,
    )


@router.get(
    "/{student_id}",
    response_model=StudentRead,
    summary="Get student by ID",
)
async def get_student(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a student by ID. Students can view their own record."""
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # RBAC: students can only view their own record
    if current_user.role == UserRole.STUDENT and student.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    return student


@router.patch(
    "/{student_id}",
    response_model=StudentRead,
    summary="Update student",
)
async def update_student(
    student_id: int,
    body: StudentUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Update a student's information."""
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(student, field, value)

    await db.flush()
    await db.refresh(student)
    return student


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete student",
)
async def delete_student(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Delete a student record."""
    result = await db.execute(select(Student).where(Student.id == student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    await db.delete(student)


@router.delete(
    "/{student_id}/biometric-data",
    summary="Delete student biometric data and anonymize identity",
)
async def delete_student_biometric_data(
    student_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """Right-to-deletion endpoint for biometric templates and identifying metadata."""
    try:
        result = await delete_student_biometric_data_async(
            db,
            student_id=student_id,
            actor_user_id=admin_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "message": "Biometric data deleted and student identity anonymized",
        **result,
    }


@router.post(
    "/enroll",
    response_model=EmbeddingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll via raw embedding",
)
async def enroll_from_embedding(
    body: EnrollFromEmbeddingRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role(UserRole.ADMIN, UserRole.INSTRUCTOR)),
):
    """Enroll a student by directly providing a 512-d embedding vector."""
    # Verify student exists
    result = await db.execute(select(Student).where(Student.id == body.student_id))
    student = result.scalar_one_or_none()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    embedding = StudentEmbedding(
        student_id=body.student_id,
        pose_label=body.pose_label,
        resolution=body.resolution,
        model_name=_ENROLLMENT_MODEL_NAME,
        embedding=body.embedding,
        embedding_norm=1.0,
        retention_score=1.0,
        template_status="active",
        is_active=True,
    )
    db.add(embedding)

    # Mark as enrolled
    student.is_enrolled = True

    await db.flush()
    await db.refresh(embedding)
    return embedding
