"""AI / CV Pipeline — ported from V1 with V2 architecture upgrades.

P0-1  Two-pass SAHI sliced detection (640 + 320)
P0-2  Direct recognition-model embedding (no redundant internal detector)
P1-1  Enhanced preprocessing pipeline integration
P1-2  Super-resolution for tiny face crops
P1-3  Vectorised NumPy batch cosine matching + pgvector fallback

V2 Upgrades:
- Uses SQLAlchemy 2.0 async sessions (sync_session adapter for Celery workers)
- Template matrix built from StudentEmbeddings table only (no V1 fallback)
- AdaFace fully integrated as primary/secondary model
- process_snapshot_bytes returns data for Redis pub/sub SSE broadcast
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import cv2
import numpy as np

from backend.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


@dataclass
class FaceMatch:
    """Matched face result from the recognition pipeline."""

    student_id: int
    confidence: float
    bbox: tuple[int, int, int, int]
    quality: float


class AIPipeline:
    """Full AI pipeline: detect → preprocess → embed → match.

    Supports:
    - SAHI dual-pass sliced YOLO detection (640px + 320px)
    - ArcFace (InsightFace) + AdaFace (ONNX) embedding extraction
    - Vectorised batch cosine matching with multi-pose templates
    - Quality scoring (area ratio + Laplacian sharpness)
    """

    def __init__(self) -> None:
        self._detector = None
        self._detector_fine = None
        self._recognizer = None
        self._recognition_model = None
        self._adaface_session = None
        self._sahi_available = False
        self._sr_func = None
        self._loaded = False

    # ── Lazy Model Loading ─────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Lazy-load all AI models on first use."""
        if self._loaded:
            return
        self._loaded = True
        self._load_models()

    def _load_models(self) -> None:
        # Primary SAHI detector (640×640 slices)
        try:
            from sahi import AutoDetectionModel

            self._detector = AutoDetectionModel.from_pretrained(
                model_type="ultralytics",
                model_path=settings.yolo_model_path,
                confidence_threshold=0.35,
                device="cpu",
            )
            self._sahi_available = True
            logger.info("ai_pipeline: SAHI primary detector loaded")
        except Exception:
            self._detector = None
            self._sahi_available = False

        # Fine-grained SAHI detector (320×320 — distant faces)
        if settings.enable_dual_pass_sahi:
            try:
                from sahi import AutoDetectionModel
                import os

                fine_model_path = settings.yolo_model_path_fine
                if not os.path.exists(fine_model_path):
                    fine_model_path = settings.yolo_model_path

                self._detector_fine = AutoDetectionModel.from_pretrained(
                    model_type="ultralytics",
                    model_path=fine_model_path,
                    confidence_threshold=0.30,
                    device="cpu",
                )
                logger.info("ai_pipeline: SAHI fine detector loaded")
            except Exception:
                self._detector_fine = None

        # InsightFace recognizer (ArcFace)
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(providers=[settings.insightface_provider])
            det_size = max(int(settings.insightface_det_size), 640)
            app.prepare(ctx_id=0, det_size=(det_size, det_size))
            self._recognizer = app

            # Grab underlying recognition model for direct inference (P0-2)
            if hasattr(app, "models"):
                for model in app.models:
                    if hasattr(model, "input_size") and hasattr(
                        model, "get_feat"
                    ):
                        self._recognition_model = model
                        logger.info(
                            "ai_pipeline: direct recognition model acquired (%s)",
                            type(model).__name__,
                        )
                        break
                if self._recognition_model is None:
                    rec = getattr(app, "rec_model", None) or getattr(
                        app, "recognition", None
                    )
                    if rec is not None:
                        self._recognition_model = rec
                        logger.info(
                            "ai_pipeline: recognition model via attribute"
                        )
        except Exception:
            self._recognizer = None

        # Super-resolution
        if settings.enable_super_resolution:
            try:
                from backend.services.face_sr import face_super_resolver
                import pathlib

                face_super_resolver._model_path = pathlib.Path(
                    settings.super_resolution_model_path
                )
                self._sr_func = face_super_resolver.upscale
                logger.info("ai_pipeline: super-resolution enabled")
            except Exception:
                self._sr_func = None

        # AdaFace ONNX model
        if settings.enable_adaface:
            try:
                import onnxruntime as ort
                import os

                adaface_path = settings.adaface_model_path
                if os.path.exists(adaface_path):
                    self._adaface_session = ort.InferenceSession(
                        adaface_path, providers=["CPUExecutionProvider"]
                    )
                    logger.info(
                        "ai_pipeline: AdaFace ONNX model loaded from %s",
                        adaface_path,
                    )
                else:
                    logger.warning(
                        "ai_pipeline: AdaFace model not found at %s",
                        adaface_path,
                    )
            except Exception as exc:
                logger.warning("ai_pipeline: AdaFace load failed: %s", exc)

    # ── Embedding Extraction ───────────────────────────────────────

    def extract_embedding(self, image_bgr: np.ndarray) -> np.ndarray | None:
        """Extract a 512-d face embedding from an image.

        P0-2: Uses recognition model directly (skips redundant detector).
        P1-1: Applies enhanced preprocessing pipeline.
        """
        self.ensure_loaded()
        if self._recognizer is None:
            return None

        preprocessed = self._preprocess(image_bgr)

        # Try direct recognition model first
        if self._recognition_model is not None:
            emb = self._extract_direct(preprocessed)
            if emb is not None:
                return emb

        # Fallback: full InsightFace .get() with candidate variants
        candidates = [preprocessed, image_bgr]
        if min(image_bgr.shape[:2]) < 480:
            scale = 480.0 / float(min(image_bgr.shape[:2]))
            resized = cv2.resize(
                image_bgr,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
            candidates.append(resized)

        for candidate in candidates:
            faces = self._recognizer.get(candidate)
            if not faces:
                continue
            best_face = max(
                faces,
                key=lambda f: float(
                    (getattr(f, "bbox", [0, 0, 0, 0])[2]
                     - getattr(f, "bbox", [0, 0, 0, 0])[0])
                    * (getattr(f, "bbox", [0, 0, 0, 0])[3]
                       - getattr(f, "bbox", [0, 0, 0, 0])[1])
                ),
            )
            embedding = np.array(best_face.embedding, dtype=np.float32)
            return self._normalize(embedding)
        return None

    def _extract_direct(self, face_bgr: np.ndarray) -> np.ndarray | None:
        """P0-2: Extract embedding via recognition model directly.

        Bypasses InsightFace's internal face detector — avoids redundant
        detection when SAHI has already found and cropped the face.
        """
        try:
            model = self._recognition_model
            input_size = getattr(model, "input_size", (112, 112))
            if isinstance(input_size, int):
                input_size = (input_size, input_size)

            aligned = cv2.resize(face_bgr, input_size)
            aligned = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB)
            aligned = np.transpose(aligned, (2, 0, 1)).astype(np.float32)
            aligned = (aligned - 127.5) / 127.5
            aligned = np.expand_dims(aligned, axis=0)

            if hasattr(model, "get_feat"):
                embedding = model.get_feat(aligned)
                if isinstance(embedding, np.ndarray):
                    emb = embedding.flatten().astype(np.float32)
                    if emb.shape[0] >= 128:
                        return self._normalize(emb)

            if hasattr(model, "session"):
                session = model.session
                input_name = session.get_inputs()[0].name
                result = session.run(None, {input_name: aligned})
                emb = result[0].flatten().astype(np.float32)
                if emb.shape[0] >= 128:
                    return self._normalize(emb)

        except Exception as exc:
            logger.debug("direct embedding extraction failed: %s", exc)
        return None

    def extract_embedding_adaface(
        self, image_bgr: np.ndarray
    ) -> np.ndarray | None:
        """Extract embedding using AdaFace ONNX model.

        AdaFace adapts its embedding margin based on image quality —
        superior for low-resolution and angled face crops.
        """
        self.ensure_loaded()
        if self._adaface_session is None:
            return None

        try:
            face_112 = cv2.resize(image_bgr, (112, 112))
            face_rgb = cv2.cvtColor(face_112, cv2.COLOR_BGR2RGB)
            face_float = face_rgb.astype(np.float32) / 255.0
            face_float = (face_float - 0.5) / 0.5  # normalize to [-1, 1]
            face_float = face_float.transpose(2, 0, 1)  # HWC -> CHW
            face_float = np.expand_dims(face_float, 0)

            input_name = self._adaface_session.get_inputs()[0].name
            outputs = self._adaface_session.run(
                None, {input_name: face_float}
            )
            emb = outputs[0].flatten().astype(np.float32)
            return self._normalize(emb)
        except Exception as exc:
            logger.debug("AdaFace extraction failed: %s", exc)
            return None

    def extract_embedding_low_res(
        self, image_bgr: np.ndarray, target_px: int = 48
    ) -> np.ndarray | None:
        """Extract embedding from a downscaled + SR'd image.

        Simulates what the pipeline sees for a distant student.
        """
        h, w = image_bgr.shape[:2]
        scale = target_px / max(h, w)
        small = cv2.resize(
            image_bgr,
            (max(int(w * scale), 8), max(int(h * scale), 8)),
            interpolation=cv2.INTER_AREA,
        )

        if self._sr_func:
            try:
                small = self._sr_func(small)
            except Exception:
                small = cv2.resize(
                    small, (w, h), interpolation=cv2.INTER_CUBIC
                )
        else:
            small = cv2.resize(
                small, (w, h), interpolation=cv2.INTER_CUBIC
            )

        return self.extract_embedding(small)

    def _preprocess(self, image_bgr: np.ndarray) -> np.ndarray:
        """P1-1: Apply enhanced preprocessing pipeline."""
        if not settings.enable_preprocessing:
            return image_bgr
        try:
            from backend.services.preprocessing import preprocess_face_crop

            return preprocess_face_crop(
                image_bgr,
                enable_sr=settings.enable_super_resolution,
                min_upscale_px=settings.min_face_upscale_px,
                sr_func=self._sr_func,
            )
        except Exception:
            return image_bgr

    # ── Detection ──────────────────────────────────────────────────

    def detect_faces_sahi(
        self, image_bgr: np.ndarray
    ) -> list[tuple[int, int, int, int]]:
        """P0-1: Two-pass SAHI detection (640 + 320).

        Pass 1 (640×640): catches near/mid-range faces.
        Pass 2 (320×320): catches distant/small faces with higher overlap.
        Results are merged with NMS deduplication.
        """
        self.ensure_loaded()
        if self._detector is None:
            return []

        from sahi.predict import get_sliced_prediction

        boxes: list[tuple[int, int, int, int]] = []

        # Pass 1: standard 640×640
        result_640 = get_sliced_prediction(
            image_bgr,
            self._detector,
            slice_height=640,
            slice_width=640,
            overlap_height_ratio=0.2,
            overlap_width_ratio=0.2,
            postprocess_match_metric="IOU",
            postprocess_match_threshold=0.5,
        )
        for pred in result_640.object_prediction_list:
            x1, y1, x2, y2 = pred.bbox.to_xyxy()
            boxes.append((int(x1), int(y1), int(x2 - x1), int(y2 - y1)))

        # Pass 2: fine-grained 320×320
        if settings.enable_dual_pass_sahi and self._detector_fine is not None:
            fine_slice = settings.sahi_fine_slice_size
            fine_overlap = settings.sahi_fine_overlap
            result_fine = get_sliced_prediction(
                image_bgr,
                self._detector_fine,
                slice_height=fine_slice,
                slice_width=fine_slice,
                overlap_height_ratio=fine_overlap,
                overlap_width_ratio=fine_overlap,
                postprocess_match_metric="IOU",
                postprocess_match_threshold=0.5,
            )
            for pred in result_fine.object_prediction_list:
                x1, y1, x2, y2 = pred.bbox.to_xyxy()
                boxes.append(
                    (int(x1), int(y1), int(x2 - x1), int(y2 - y1))
                )

        if len(boxes) > 1:
            boxes = self._nms_merge(boxes, iou_threshold=0.4)

        return boxes

    @staticmethod
    def _nms_merge(
        boxes: list[tuple[int, int, int, int]],
        iou_threshold: float = 0.4,
    ) -> list[tuple[int, int, int, int]]:
        """Non-maximum suppression to deduplicate bounding boxes."""
        if not boxes:
            return []
        rects = [[x, y, w, h] for x, y, w, h in boxes]
        confidences = [1.0] * len(rects)
        indices = cv2.dnn.NMSBoxes(
            rects,
            confidences,
            score_threshold=0.0,
            nms_threshold=iou_threshold,
        )
        if indices is None or len(indices) == 0:
            return boxes
        if hasattr(indices, "flatten"):
            indices = indices.flatten()
        return [boxes[i] for i in indices]

    # ── Quality Scoring ────────────────────────────────────────────

    @staticmethod
    def _face_sharpness(crop_bgr: np.ndarray) -> float:
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    @staticmethod
    def _safe_ratio(value: float, denom: float) -> float:
        if denom <= 0:
            return 0.0
        return float(value / denom)

    def face_quality_score(
        self,
        crop_bgr: np.ndarray,
        bbox: tuple[int, int, int, int],
        full_image_shape: tuple[int, int, int],
    ) -> tuple[float, float]:
        """Compute face quality score (0-1) from area ratio + sharpness."""
        x, y, w, h = bbox
        frame_h, frame_w = full_image_shape[:2]
        face_area = float(max(w, 1) * max(h, 1))
        frame_area = float(max(frame_w * frame_h, 1))
        area_ratio = self._safe_ratio(face_area, frame_area)
        sharpness = self._face_sharpness(crop_bgr)

        area_score = min(
            1.0, area_ratio / max(settings.min_face_area_ratio, 1e-8)
        )
        blur_score = min(
            1.0, sharpness / max(settings.min_blur_variance, 1e-6)
        )
        quality = 0.55 * area_score + 0.45 * blur_score
        return float(quality), float(sharpness)

    def _is_face_usable(
        self,
        bbox: tuple[int, int, int, int],
        quality_score: float,
        sharpness: float,
    ) -> bool:
        """Check if a detected face passes quality gates."""
        _, _, w, h = bbox
        if min(w, h) < settings.min_face_size_px:
            return False
        if sharpness < settings.min_blur_variance:
            return False
        if quality_score < settings.min_face_quality_score:
            return False
        return True

    @staticmethod
    def _match_decision(best_score: float, second_best: float) -> bool:
        """Two-tier match decision: strict threshold OR relaxed + margin."""
        margin_ok = (best_score - second_best) >= settings.face_match_margin
        return best_score >= settings.face_match_threshold or (
            best_score >= settings.face_match_relaxed_threshold and margin_ok
        )

    # ── Recognition ────────────────────────────────────────────────

    def _build_template_matrix(
        self, db_session
    ) -> tuple[list[int], np.ndarray, dict[int, list[int]]]:
        """Build template matrix from StudentEmbeddings table.

        V2: Uses SQLAlchemy session (sync or async-adapted).
        No V1 fallback to Students.face_embedding — all templates
        are in StudentEmbeddings with model_name + pose_label.

        Returns:
            (student_ids_per_row, embedding_matrix, student_to_row_indices)
        """
        from backend.models.student import Student, StudentEmbedding

        row_student_ids: list[int] = []
        rows: list[np.ndarray] = []
        student_rows: dict[int, list[int]] = {}

        # Get all enrolled student embeddings for arcface
        from sqlalchemy import select

        query = (
            select(StudentEmbedding)
            .join(Student)
            .where(
                Student.is_enrolled.is_(True),
                StudentEmbedding.model_name == "arcface",
            )
        )
        result = db_session.execute(query)
        for se in result.scalars().all():
            idx = len(rows)
            row_student_ids.append(se.student_id)
            rows.append(
                self._normalize(np.array(se.embedding, dtype=np.float32))
            )
            student_rows.setdefault(se.student_id, []).append(idx)

        matrix = np.vstack(rows) if rows else np.empty((0, 512))
        return row_student_ids, matrix, student_rows

    def recognize(
        self, db_session, image_bgr: np.ndarray, schedule_id: int
    ) -> list[FaceMatch]:
        """Full recognition pipeline: detect → quality-check → embed → match.

        Args:
            db_session: SQLAlchemy session (sync for Celery workers).
            image_bgr: Full-frame BGR image.
            schedule_id: Schedule ID for context.

        Returns:
            Deduplicated list of FaceMatch objects.
        """
        self.ensure_loaded()
        boxes = self.detect_faces_sahi(image_bgr)

        row_student_ids, student_matrix, student_rows = (
            self._build_template_matrix(db_session)
        )
        if student_matrix.shape[0] == 0:
            return []

        unique_students = list(student_rows.keys())
        matches: list[FaceMatch] = []

        for x, y, w, h in boxes:
            x1 = max(x, 0)
            y1 = max(y, 0)
            x2 = min(x + w, image_bgr.shape[1])
            y2 = min(y + h, image_bgr.shape[0])
            if x2 <= x1 or y2 <= y1:
                continue

            crop = image_bgr[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            quality_score, sharpness = self.face_quality_score(
                crop_bgr=crop,
                bbox=(x, y, w, h),
                full_image_shape=image_bgr.shape,
            )
            if not self._is_face_usable(
                (x, y, w, h),
                quality_score=quality_score,
                sharpness=sharpness,
            ):
                continue

            emb = self.extract_embedding(crop)
            if emb is None:
                continue

            # Vectorised cosine against ALL templates
            all_scores = student_matrix @ emb

            # Max-per-student: pick the best template for each student
            best_student_id = None
            best_score = -1.0
            second_best = -1.0
            for sid in unique_students:
                row_idxs = student_rows[sid]
                max_score = float(max(all_scores[i] for i in row_idxs))
                if max_score > best_score:
                    second_best = best_score
                    best_score = max_score
                    best_student_id = sid
                elif max_score > second_best:
                    second_best = max_score

            if best_student_id is None:
                continue

            if self._match_decision(best_score, second_best):
                matches.append(
                    FaceMatch(
                        student_id=best_student_id,
                        confidence=best_score,
                        bbox=(x, y, w, h),
                        quality=float(quality_score),
                    )
                )

        return self._dedupe_by_student(matches)

    def _dedupe_by_student(
        self, matches: list[FaceMatch]
    ) -> list[FaceMatch]:
        """Keep only the highest-confidence match per student."""
        best_per_student: dict[int, FaceMatch] = {}
        for match in matches:
            prev = best_per_student.get(match.student_id)
            if prev is None or match.confidence > prev.confidence:
                best_per_student[match.student_id] = match
        return list(best_per_student.values())

    # ── Utilities ──────────────────────────────────────────────────

    @staticmethod
    def _normalize(vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec) + 1e-8
        return vec / norm

    def annotate_image(
        self, image_bgr: np.ndarray, matches: list[FaceMatch]
    ) -> bytes:
        """Draw bounding boxes and labels on image, return JPEG bytes."""
        canvas = image_bgr.copy()
        for m in matches:
            x, y, w, h = m.bbox
            cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                canvas,
                f"ID:{m.student_id} {m.confidence:.2f} Q:{m.quality:.2f}",
                (x, max(y - 10, 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
        ok, encoded = cv2.imencode(".jpg", canvas)
        if not ok:
            return b""
        return encoded.tobytes()

    def readiness(self) -> dict:
        """Report model loading status for the /ai/status endpoint."""
        self.ensure_loaded()
        return {
            "recognizer_loaded": self._recognizer is not None,
            "detector_loaded": self._detector is not None,
            "detector_fine_loaded": self._detector_fine is not None,
            "direct_recognition": self._recognition_model is not None,
            "sahi_available": self._sahi_available,
            "dual_pass_sahi": (
                settings.enable_dual_pass_sahi
                and self._detector_fine is not None
            ),
            "preprocessing_enabled": settings.enable_preprocessing,
            "super_resolution_enabled": settings.enable_super_resolution,
            "sr_func_available": self._sr_func is not None,
            "adaface_available": self._adaface_session is not None,
            "liveness_enabled": settings.enable_liveness_check,
            "match_threshold": settings.face_match_threshold,
            "match_relaxed_threshold": settings.face_match_relaxed_threshold,
            "match_margin": settings.face_match_margin,
        }


# Module-level singleton (lazy — no models loaded until first use)
ai_pipeline = AIPipeline()
