"""AI / CV Pipeline — ported from V1 with V2 architecture upgrades.

P0-1  Two-pass SAHI sliced detection (640 + 320)
P0-2  Direct recognition-model embedding (no redundant internal detector)
P1-1  Enhanced preprocessing pipeline integration
P1-2  Super-resolution for tiny face crops
P1-3  Vectorised NumPy batch cosine matching + pgvector fallback

V2 Upgrades:
- Uses SQLAlchemy 2.0 async sessions (sync_session adapter for Celery workers)
- Template matrix built from StudentEmbeddings table only (no V1 fallback)
- AdaFace + LVFace integrated as optional secondary/tertiary models
- process_snapshot_bytes returns data for Redis pub/sub SSE broadcast
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.core.constants import EMBEDDING_DIMENSION
from backend.core.config import build_onnx_execution_providers, get_settings
from backend.db.vector import (
    VectorSearchFilters,
    find_nearest_faces_diskann_sync,
    find_nearest_faces_sync,
    is_diskann_ready_sync,
)
from backend.services.inference_stats import inference_stats

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
    - ArcFace (InsightFace) + AdaFace/LVFace (ONNX) embedding extraction
    - Vectorised batch cosine matching with multi-pose templates
    - Quality scoring (area ratio + Laplacian sharpness)
    """

    def __init__(self) -> None:
        self._detector = None
        self._detector_fine = None
        self._detector_yolov8 = None
        self._detector_yolov8_fine = None
        self._detector_yolov12 = None
        self._detector_yolov12_fine = None
        self._yolov12_native_model = None
        self._yolo26_native_model = None
        self._recognizer = None
        self._recognition_model = None
        self._adaface_session = None
        self._lvface_session = None
        self._lvface_input_size = (112, 112)
        self._lvface_input_name: str | None = None
        self._embedding_dim_warning_models: set[str] = set()
        self._sahi_available = False
        self._sr_func = None
        self._sr_func_local = None
        self._codeformer_service = None
        self._codeformer_func = None
        self._triton_client = None
        self._triton_fallback_logged: set[str] = set()
        self._loaded = False
        self._restoration_stats: dict[str, float] = {
            "codeformer_attempts": 0.0,
            "codeformer_applied": 0.0,
            "codeformer_discarded": 0.0,
            "codeformer_latency_total_ms": 0.0,
            "codeformer_latency_samples": 0.0,
        }
        self._runtime_settings_path = (
            Path(__file__).resolve().parents[1] / "data" / "system_settings.json"
        )
        self._runtime_settings_cache: dict | None = None
        self._runtime_settings_mtime: float | None = None

    # ── Runtime Gates ────────────────────────────────────────

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return float(max(low, min(high, value)))

    def _default_runtime_gates(self) -> dict:
        primary_model = "arcface"
        if settings.enable_lvface:
            primary_model = "lvface"
        elif settings.enable_adaface:
            primary_model = "adaface"

        return {
            "primary_model": primary_model,
            "recognition_fusion_mode": str(settings.recognition_fusion_mode),
            "forced_model": None,
            "enable_diskann": bool(settings.enable_diskann),
            "ann_retrieval_backend": str(settings.ann_retrieval_backend),
            "ann_search_k": int(settings.ann_search_k),
            "ann_filter_active_only": bool(settings.ann_filter_active_only),
            "ann_filter_exclude_quarantined": bool(settings.ann_filter_exclude_quarantined),
            "ann_filter_enrollment_year": settings.ann_filter_enrollment_year,
            "ann_filter_department": settings.ann_filter_department,
            "face_match_threshold": float(settings.face_match_threshold),
            "face_match_relaxed_threshold": float(settings.face_match_relaxed_threshold),
            "lvface_match_threshold": float(settings.lvface_match_threshold),
            "lvface_match_relaxed_threshold": float(settings.lvface_match_relaxed_threshold),
            "face_match_margin": float(settings.face_match_margin),
            "min_face_size_px": int(settings.min_face_size_px),
            "min_face_area_ratio": float(settings.min_face_area_ratio),
            "min_blur_variance": float(settings.min_blur_variance),
            "min_face_quality_score": float(settings.min_face_quality_score),
            "detector_confidence_threshold": float(settings.detector_confidence_threshold),
            "detector_nms_iou_threshold": float(settings.detector_nms_iou_threshold),
            "arcface_weight": float(settings.arcface_weight),
            "adaface_weight": float(settings.adaface_weight),
            "lvface_weight": float(settings.lvface_weight),
            "adaface_fusion_weight": 0.65 if primary_model == "adaface" else 0.35,
            "enable_codeformer": bool(settings.enable_codeformer),
            "codeformer_min_face_px": int(settings.codeformer_min_face_px),
            "codeformer_quality_threshold": float(settings.codeformer_quality_threshold),
            "codeformer_max_per_frame": int(settings.codeformer_max_per_frame),
            "codeformer_fidelity_weight": float(settings.codeformer_fidelity_weight),
            "codeformer_identity_preservation_threshold": float(
                settings.codeformer_identity_preservation_threshold
            ),
        }

    def get_runtime_gates(self) -> dict:
        """Load deployment-tunable gates from persisted system settings."""
        defaults = self._default_runtime_gates()

        mtime: float | None = None
        if self._runtime_settings_path.exists():
            try:
                mtime = self._runtime_settings_path.stat().st_mtime
            except Exception:
                mtime = None

        if (
            self._runtime_settings_cache is not None
            and mtime is not None
            and self._runtime_settings_mtime == mtime
        ):
            return dict(self._runtime_settings_cache)

        merged = dict(defaults)
        if self._runtime_settings_path.exists():
            try:
                raw = json.loads(self._runtime_settings_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    primary = str(raw.get("primary_model", merged["primary_model"]))
                    normalized_primary = primary.lower()
                    merged["primary_model"] = (
                        normalized_primary
                        if normalized_primary in {"arcface", "adaface", "lvface"}
                        else "arcface"
                    )

                    if "recognition_fusion_mode" in raw:
                        merged["recognition_fusion_mode"] = str(raw["recognition_fusion_mode"])
                    if "forced_model" in raw:
                        forced_model = raw["forced_model"]
                        merged["forced_model"] = (
                            str(forced_model).lower()
                            if forced_model is not None
                            else None
                        )
                    if "enable_diskann" in raw:
                        merged["enable_diskann"] = bool(raw["enable_diskann"])
                    if "ann_retrieval_backend" in raw:
                        merged["ann_retrieval_backend"] = str(raw["ann_retrieval_backend"])
                    if "ann_search_k" in raw:
                        merged["ann_search_k"] = int(raw["ann_search_k"])
                    if "ann_filter_active_only" in raw:
                        merged["ann_filter_active_only"] = bool(raw["ann_filter_active_only"])
                    if "ann_filter_exclude_quarantined" in raw:
                        merged["ann_filter_exclude_quarantined"] = bool(
                            raw["ann_filter_exclude_quarantined"]
                        )
                    if "ann_filter_enrollment_year" in raw:
                        enrollment_year = raw["ann_filter_enrollment_year"]
                        merged["ann_filter_enrollment_year"] = (
                            int(enrollment_year) if enrollment_year is not None else None
                        )
                    if "ann_filter_department" in raw:
                        department = raw["ann_filter_department"]
                        merged["ann_filter_department"] = (
                            str(department).strip() if department is not None else None
                        )

                    if "confidence_threshold" in raw:
                        merged["face_match_threshold"] = float(raw["confidence_threshold"])
                    if "face_match_relaxed_threshold" in raw:
                        merged["face_match_relaxed_threshold"] = float(
                            raw["face_match_relaxed_threshold"]
                        )
                    if "lvface_match_threshold" in raw:
                        merged["lvface_match_threshold"] = float(raw["lvface_match_threshold"])
                    if "lvface_match_relaxed_threshold" in raw:
                        merged["lvface_match_relaxed_threshold"] = float(
                            raw["lvface_match_relaxed_threshold"]
                        )
                    if "face_match_margin" in raw:
                        merged["face_match_margin"] = float(raw["face_match_margin"])
                    if "min_face_size_px" in raw:
                        merged["min_face_size_px"] = int(raw["min_face_size_px"])
                    if "min_face_area_ratio" in raw:
                        merged["min_face_area_ratio"] = float(raw["min_face_area_ratio"])
                    if "min_blur_variance" in raw:
                        merged["min_blur_variance"] = float(raw["min_blur_variance"])
                    if "min_face_quality_score" in raw:
                        merged["min_face_quality_score"] = float(raw["min_face_quality_score"])
                    if "detector_confidence_threshold" in raw:
                        merged["detector_confidence_threshold"] = float(
                            raw["detector_confidence_threshold"]
                        )
                    if "detector_nms_iou_threshold" in raw:
                        merged["detector_nms_iou_threshold"] = float(
                            raw["detector_nms_iou_threshold"]
                        )
                    if "arcface_weight" in raw:
                        merged["arcface_weight"] = float(raw["arcface_weight"])
                    if "adaface_fusion_weight" in raw:
                        merged["adaface_fusion_weight"] = float(raw["adaface_fusion_weight"])
                    if "adaface_weight" in raw:
                        merged["adaface_weight"] = float(raw["adaface_weight"])
                    if "lvface_weight" in raw:
                        merged["lvface_weight"] = float(raw["lvface_weight"])
                    if "enable_codeformer" in raw:
                        merged["enable_codeformer"] = bool(raw["enable_codeformer"])
                    if "codeformer_min_face_px" in raw:
                        merged["codeformer_min_face_px"] = int(raw["codeformer_min_face_px"])
                    if "codeformer_quality_threshold" in raw:
                        merged["codeformer_quality_threshold"] = float(
                            raw["codeformer_quality_threshold"]
                        )
                    if "codeformer_max_per_frame" in raw:
                        merged["codeformer_max_per_frame"] = int(raw["codeformer_max_per_frame"])
                    if "codeformer_fidelity_weight" in raw:
                        merged["codeformer_fidelity_weight"] = float(raw["codeformer_fidelity_weight"])
                    if "codeformer_identity_preservation_threshold" in raw:
                        merged["codeformer_identity_preservation_threshold"] = float(
                            raw["codeformer_identity_preservation_threshold"]
                        )

                    # Backward compatibility: legacy two-model weight setting.
                    if (
                        "adaface_fusion_weight" in raw
                        and "adaface_weight" not in raw
                        and "arcface_weight" not in raw
                    ):
                        adaface_weight = float(raw["adaface_fusion_weight"])
                        merged["adaface_weight"] = adaface_weight
                        merged["arcface_weight"] = 1.0 - adaface_weight
                        if "lvface_weight" not in raw:
                            merged["lvface_weight"] = 0.0
            except Exception as exc:
                logger.warning("ai_pipeline: failed to load runtime settings: %s", exc)

        merged["face_match_threshold"] = self._clamp(
            float(merged["face_match_threshold"]), 0.5, 1.0
        )
        merged["face_match_relaxed_threshold"] = self._clamp(
            float(merged["face_match_relaxed_threshold"]),
            0.5,
            float(merged["face_match_threshold"]),
        )
        merged["lvface_match_threshold"] = self._clamp(
            float(merged["lvface_match_threshold"]),
            0.5,
            1.0,
        )
        merged["lvface_match_relaxed_threshold"] = self._clamp(
            float(merged["lvface_match_relaxed_threshold"]),
            0.5,
            float(merged["lvface_match_threshold"]),
        )
        merged["face_match_margin"] = self._clamp(
            float(merged["face_match_margin"]), 0.0, 0.25
        )
        merged["enable_diskann"] = bool(merged.get("enable_diskann", False))
        merged["ann_search_k"] = int(
            self._clamp(float(merged.get("ann_search_k", 64)), 1.0, 512.0)
        )
        merged["ann_filter_active_only"] = bool(
            merged.get("ann_filter_active_only", False)
        )
        merged["ann_filter_exclude_quarantined"] = bool(
            merged.get("ann_filter_exclude_quarantined", True)
        )
        enrollment_year = merged.get("ann_filter_enrollment_year")
        merged["ann_filter_enrollment_year"] = (
            int(enrollment_year) if enrollment_year is not None else None
        )
        department = merged.get("ann_filter_department")
        if department is None:
            merged["ann_filter_department"] = None
        else:
            department_value = str(department).strip()
            merged["ann_filter_department"] = department_value or None
        merged["min_face_size_px"] = int(
            self._clamp(float(merged["min_face_size_px"]), 24.0, 256.0)
        )
        merged["min_face_area_ratio"] = self._clamp(
            float(merged["min_face_area_ratio"]), 0.0005, 0.08
        )
        merged["min_blur_variance"] = self._clamp(
            float(merged["min_blur_variance"]), 5.0, 500.0
        )
        merged["min_face_quality_score"] = self._clamp(
            float(merged["min_face_quality_score"]), 0.0, 1.0
        )
        merged["detector_confidence_threshold"] = self._clamp(
            float(merged["detector_confidence_threshold"]), 0.05, 0.95
        )
        merged["detector_nms_iou_threshold"] = self._clamp(
            float(merged["detector_nms_iou_threshold"]), 0.1, 0.95
        )
        merged["adaface_fusion_weight"] = self._clamp(
            float(merged["adaface_fusion_weight"]), 0.0, 1.0
        )
        merged["arcface_weight"] = self._clamp(float(merged["arcface_weight"]), 0.0, 1.0)
        merged["adaface_weight"] = self._clamp(float(merged["adaface_weight"]), 0.0, 1.0)
        merged["lvface_weight"] = self._clamp(float(merged["lvface_weight"]), 0.0, 1.0)
        merged["codeformer_min_face_px"] = int(
            self._clamp(float(merged["codeformer_min_face_px"]), 16.0, 256.0)
        )
        merged["codeformer_quality_threshold"] = self._clamp(
            float(merged["codeformer_quality_threshold"]), 0.0, 1.0
        )
        merged["codeformer_max_per_frame"] = int(
            self._clamp(float(merged["codeformer_max_per_frame"]), 0.0, 20.0)
        )
        merged["codeformer_fidelity_weight"] = self._clamp(
            float(merged["codeformer_fidelity_weight"]), 0.0, 1.0
        )
        merged["codeformer_identity_preservation_threshold"] = self._clamp(
            float(merged["codeformer_identity_preservation_threshold"]), 0.0, 1.0
        )

        fusion_mode = str(merged.get("recognition_fusion_mode", "weighted_average")).lower()
        if fusion_mode not in {
            "arcface_only",
            "adaface_only",
            "lvface_only",
            "weighted_average",
            "max_confidence",
        }:
            fusion_mode = "weighted_average"
        merged["recognition_fusion_mode"] = fusion_mode

        ann_backend = str(merged.get("ann_retrieval_backend", "numpy")).lower()
        if ann_backend not in {"numpy", "hnsw", "diskann"}:
            ann_backend = "numpy"
        merged["ann_retrieval_backend"] = ann_backend

        forced_model = merged.get("forced_model")
        if forced_model is not None:
            forced_model = str(forced_model).lower()
            if forced_model not in {"arcface", "adaface", "lvface"}:
                forced_model = None
        merged["forced_model"] = forced_model

        self._runtime_settings_cache = dict(merged)
        self._runtime_settings_mtime = mtime
        return merged

    def model_fusion_weights(
        self,
        runtime_gates: dict | None = None,
        *,
        available_models: set[str],
    ) -> dict[str, float]:
        """Return normalized fusion weights for available recognition models."""
        weights = {
            "arcface": 0.0,
            "adaface": 0.0,
            "lvface": 0.0,
        }
        if not available_models:
            return weights

        gates = runtime_gates or self.get_runtime_gates()
        forced_model = gates.get("forced_model")
        if isinstance(forced_model, str) and forced_model in available_models:
            weights[forced_model] = 1.0
            return weights

        fusion_mode = str(gates.get("recognition_fusion_mode", "weighted_average"))
        if fusion_mode in {"arcface_only", "adaface_only", "lvface_only"}:
            selected = fusion_mode.replace("_only", "")
            if selected in available_models:
                weights[selected] = 1.0
                return weights

        configured = {
            "arcface": self._clamp(float(gates.get("arcface_weight", 0.35)), 0.0, 1.0),
            "adaface": self._clamp(float(gates.get("adaface_weight", 0.30)), 0.0, 1.0),
            "lvface": self._clamp(float(gates.get("lvface_weight", 0.35)), 0.0, 1.0),
        }

        total = sum(configured[name] for name in available_models)
        if total <= 1e-8:
            uniform = 1.0 / float(len(available_models))
            for model_name in available_models:
                weights[model_name] = uniform
            return weights

        for model_name in available_models:
            weights[model_name] = float(configured[model_name] / total)
        return weights

    # ── Lazy Model Loading ─────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Lazy-load all AI models on first use."""
        if self._loaded:
            return
        self._loaded = True
        self._load_models()

    def _load_sahi_detector(
        self,
        model_path: str,
        confidence_threshold: float,
    ):
        from sahi import AutoDetectionModel

        return AutoDetectionModel.from_pretrained(
            model_type="ultralytics",
            model_path=model_path,
            confidence_threshold=float(confidence_threshold),
            device="cpu",
        )

    def _load_yolov12_detector(self):
        """Initialize SAHI detector for YOLOv12 model path."""
        return self._load_sahi_detector(
            model_path=settings.yolov12_model_path,
            confidence_threshold=settings.detector_confidence_threshold,
        )

    @staticmethod
    def _resolve_fine_model_path(primary_path: str, fine_path: str) -> str:
        import os

        if os.path.exists(fine_path):
            return fine_path
        return primary_path

    def _select_active_detectors(self) -> None:
        use_yolov12 = settings.enable_yolov12 and self._detector_yolov12 is not None
        if use_yolov12:
            self._detector = self._detector_yolov12
            self._detector_fine = self._detector_yolov12_fine
            logger.info("ai_pipeline: detection backend active=yolov12_sahi")
            return

        self._detector = self._detector_yolov8
        self._detector_fine = self._detector_yolov8_fine
        logger.info("ai_pipeline: detection backend active=yolov8_sahi")

    def _load_models(self) -> None:
        provider_chain = build_onnx_execution_providers(settings.insightface_provider)
        logger.info("ai_pipeline: ONNX provider chain=%s", provider_chain)

        if settings.enable_triton:
            try:
                from backend.services.triton_client import TritonInferenceClient

                self._triton_client = TritonInferenceClient(settings.triton_url)
                if self._triton_client.is_available():
                    logger.info("ai_pipeline: Triton client enabled url=%s", settings.triton_url)
                else:
                    logger.warning("ai_pipeline: Triton client configured but unavailable")
            except Exception as exc:
                self._triton_client = None
                logger.warning("ai_pipeline: Triton client init failed: %s", exc)

        # YOLOv8 SAHI detectors (default + fallback)
        try:
            self._detector_yolov8 = self._load_sahi_detector(
                model_path=settings.yolo_model_path,
                confidence_threshold=settings.detector_confidence_threshold,
            )
            self._sahi_available = True
            logger.info("ai_pipeline: SAHI YOLOv8 primary detector loaded")
        except Exception as exc:
            self._detector_yolov8 = None
            self._sahi_available = False
            logger.warning("ai_pipeline: SAHI YOLOv8 primary detector load failed: %s", exc)

        if settings.enable_dual_pass_sahi and self._detector_yolov8 is not None:
            try:
                fine_model_path = self._resolve_fine_model_path(
                    settings.yolo_model_path,
                    settings.yolo_model_path_fine,
                )
                self._detector_yolov8_fine = self._load_sahi_detector(
                    model_path=fine_model_path,
                    confidence_threshold=settings.detector_confidence_threshold,
                )
                logger.info("ai_pipeline: SAHI YOLOv8 fine detector loaded")
            except Exception as exc:
                self._detector_yolov8_fine = None
                logger.warning("ai_pipeline: SAHI YOLOv8 fine detector load failed: %s", exc)

        # YOLOv12 detector path is fully feature-flagged.
        if settings.enable_yolov12:
            try:
                self._detector_yolov12 = self._load_yolov12_detector()
                self._sahi_available = True
                logger.info("ai_pipeline: SAHI YOLOv12 primary detector loaded")
            except Exception as exc:
                self._detector_yolov12 = None
                logger.warning("ai_pipeline: SAHI YOLOv12 primary detector load failed: %s", exc)

            if settings.enable_dual_pass_sahi and self._detector_yolov12 is not None:
                try:
                    self._detector_yolov12_fine = self._load_sahi_detector(
                        model_path=settings.yolov12_model_path,
                        confidence_threshold=settings.detector_confidence_threshold,
                    )
                    logger.info("ai_pipeline: SAHI YOLOv12 fine detector loaded")
                except Exception as exc:
                    self._detector_yolov12_fine = None
                    logger.warning("ai_pipeline: SAHI YOLOv12 fine detector load failed: %s", exc)

            try:
                from ultralytics import YOLO

                self._yolov12_native_model = YOLO(settings.yolov12_model_path)
                logger.info("ai_pipeline: YOLOv12 native detector loaded")
            except Exception as exc:
                self._yolov12_native_model = None
                logger.warning("ai_pipeline: YOLOv12 native detector load failed: %s", exc)

        self._select_active_detectors()

        # InsightFace recognizer (ArcFace)
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(providers=provider_chain)
            det_size = max(int(settings.insightface_det_size), 640)
            app.prepare(ctx_id=0, det_size=(det_size, det_size))
            self._recognizer = app
            logger.info("ai_pipeline: InsightFace initialized with providers=%s", provider_chain)

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
                self._sr_func_local = face_super_resolver.upscale
                self._sr_func = self._sr_func_local
                logger.info("ai_pipeline: super-resolution enabled")
            except Exception:
                self._sr_func_local = None
                self._sr_func = None

        if settings.enable_triton and self._triton_available() and settings.enable_super_resolution:
            self._sr_func = self._triton_super_resolve

        if settings.enable_codeformer:
            try:
                from backend.services.face_restoration import codeformer_service

                self._codeformer_service = codeformer_service
                self._codeformer_func = codeformer_service.restore
                logger.info("ai_pipeline: CodeFormer restoration enabled")
            except Exception as exc:
                self._codeformer_service = None
                self._codeformer_func = None
                logger.warning("ai_pipeline: CodeFormer load failed: %s", exc)

        # AdaFace ONNX model
        if settings.enable_adaface:
            try:
                import onnxruntime as ort
                import os

                adaface_path = settings.adaface_model_path
                if os.path.exists(adaface_path):
                    self._adaface_session = ort.InferenceSession(
                        adaface_path, providers=provider_chain
                    )
                    active_providers = self._adaface_session.get_providers()
                    logger.info(
                        "ai_pipeline: AdaFace ONNX model loaded from %s (providers=%s)",
                        adaface_path,
                        active_providers,
                    )
                else:
                    logger.warning(
                        "ai_pipeline: AdaFace model not found at %s",
                        adaface_path,
                    )
            except Exception as exc:
                logger.warning("ai_pipeline: AdaFace load failed: %s", exc)

        # LVFace ONNX model (ViT-based)
        if settings.enable_lvface:
            try:
                import onnxruntime as ort
                import os

                lvface_path = settings.lvface_model_path
                if os.path.exists(lvface_path):
                    self._lvface_session = ort.InferenceSession(
                        lvface_path, providers=provider_chain
                    )
                    input_meta = self._lvface_session.get_inputs()[0]
                    self._lvface_input_name = input_meta.name

                    shape = list(getattr(input_meta, "shape", []) or [])
                    if len(shape) == 4:
                        h, w = shape[2], shape[3]
                        if isinstance(h, int) and isinstance(w, int) and h > 0 and w > 0:
                            self._lvface_input_size = (int(w), int(h))

                    active_providers = self._lvface_session.get_providers()
                    logger.info(
                        "ai_pipeline: LVFace ONNX model loaded from %s (providers=%s, input_size=%s)",
                        lvface_path,
                        active_providers,
                        self._lvface_input_size,
                    )
                else:
                    logger.warning("ai_pipeline: LVFace model not found at %s", lvface_path)
            except Exception as exc:
                logger.warning("ai_pipeline: LVFace load failed: %s", exc)

    # ── Embedding Extraction ───────────────────────────────────────

    def get_restoration_stats(self, *, reset: bool = False) -> dict[str, float]:
        """Return CodeFormer restoration telemetry used by evaluation harness."""
        snapshot = {
            "codeformer_attempts": float(self._restoration_stats["codeformer_attempts"]),
            "codeformer_applied": float(self._restoration_stats["codeformer_applied"]),
            "codeformer_discarded": float(self._restoration_stats["codeformer_discarded"]),
            "codeformer_latency_total_ms": float(
                self._restoration_stats["codeformer_latency_total_ms"]
            ),
            "codeformer_latency_samples": float(
                self._restoration_stats["codeformer_latency_samples"]
            ),
        }
        latency_samples = max(snapshot["codeformer_latency_samples"], 1.0)
        snapshot["codeformer_latency_mean_ms"] = (
            float(snapshot["codeformer_latency_total_ms"]) / float(latency_samples)
            if snapshot["codeformer_latency_samples"] > 0
            else 0.0
        )

        if reset:
            for key in self._restoration_stats:
                self._restoration_stats[key] = 0.0
        return snapshot

    @staticmethod
    def _cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        a = np.asarray(vec_a, dtype=np.float32).flatten()
        b = np.asarray(vec_b, dtype=np.float32).flatten()
        if a.size == 0 or b.size == 0 or a.shape[0] != b.shape[0]:
            return 0.0
        denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-8
        return float(np.dot(a, b) / denom)

    def _extract_arcface_embedding_raw(self, crop_bgr: np.ndarray) -> np.ndarray | None:
        if self._triton_available():
            try:
                triton_emb = self._triton_client.extract_embedding(crop_bgr, "arcface")
                if triton_emb is not None:
                    return self._project_embedding_dimension(triton_emb, source_model="arcface")
            except Exception as exc:
                self._log_triton_fallback("arcface", exc)

        if self._recognizer is None:
            return None

        if self._recognition_model is not None:
            emb = self._extract_direct(crop_bgr)
            if emb is not None:
                return emb

        candidates = [crop_bgr]
        if min(crop_bgr.shape[:2]) < 480:
            scale = 480.0 / float(min(crop_bgr.shape[:2]))
            candidates.append(
                cv2.resize(
                    crop_bgr,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_CUBIC,
                )
            )

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

    def extract_embedding(
        self,
        image_bgr: np.ndarray,
        *,
        already_preprocessed: bool = False,
        face_quality_score: float | None = None,
        codeformer_budget_context: dict[str, int] | None = None,
        restoration_mode: str | None = None,
    ) -> np.ndarray | None:
        """Extract a 512-d face embedding from an image.

        P0-2: Uses recognition model directly (skips redundant detector).
        P1-1: Applies enhanced preprocessing pipeline.
        """
        self.ensure_loaded()

        preprocessed = image_bgr if already_preprocessed else self._preprocess(
            image_bgr,
            face_quality_score=face_quality_score,
            codeformer_budget_context=codeformer_budget_context,
            restoration_mode=restoration_mode,
        )
        return self._extract_arcface_embedding_raw(preprocessed)

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

    def _project_embedding_dimension(
        self,
        embedding: np.ndarray,
        *,
        source_model: str,
    ) -> np.ndarray | None:
        """Project non-512 embeddings to VECTOR(512) compatibility shape.

        Decision: keep schema unchanged and use deterministic pad/truncate projection
        for dimension mismatches so we can A/B LVFace without a migration.
        """
        emb = np.asarray(embedding, dtype=np.float32).flatten()
        if emb.size == 0 or not np.isfinite(emb).all():
            return None

        if emb.shape[0] == EMBEDDING_DIMENSION:
            return self._normalize(emb)

        if source_model not in self._embedding_dim_warning_models:
            logger.warning(
                "ai_pipeline: %s embedding dimension=%s differs from expected=%s; applying deterministic pad/truncate projection",
                source_model,
                emb.shape[0],
                EMBEDDING_DIMENSION,
            )
            self._embedding_dim_warning_models.add(source_model)

        if emb.shape[0] > EMBEDDING_DIMENSION:
            projected = emb[:EMBEDDING_DIMENSION]
        else:
            projected = np.zeros((EMBEDDING_DIMENSION,), dtype=np.float32)
            projected[: emb.shape[0]] = emb
        return self._normalize(projected)

    def extract_embedding_adaface(
        self,
        image_bgr: np.ndarray,
        *,
        already_preprocessed: bool = False,
        face_quality_score: float | None = None,
        codeformer_budget_context: dict[str, int] | None = None,
        restoration_mode: str | None = None,
    ) -> np.ndarray | None:
        """Extract embedding using AdaFace ONNX model.

        AdaFace adapts its embedding margin based on image quality —
        superior for low-resolution and angled face crops.
        """
        self.ensure_loaded()
        source = image_bgr if already_preprocessed else self._preprocess(
            image_bgr,
            face_quality_score=face_quality_score,
            codeformer_budget_context=codeformer_budget_context,
            restoration_mode=restoration_mode,
        )
        return self._extract_embedding_adaface_raw(source)

    def _extract_embedding_adaface_raw(self, image_bgr: np.ndarray) -> np.ndarray | None:
        if self._triton_available():
            try:
                triton_emb = self._triton_client.extract_embedding(image_bgr, "adaface")
                if triton_emb is not None:
                    return self._project_embedding_dimension(triton_emb, source_model="adaface")
            except Exception as exc:
                self._log_triton_fallback("adaface", exc)

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

    def _extract_lvface_embedding(self, crop_bgr: np.ndarray) -> np.ndarray | None:
        """Extract embedding using LVFace ONNX model with ViT-style normalization."""
        if self._triton_available():
            try:
                triton_emb = self._triton_client.extract_embedding(crop_bgr, "lvface")
                if triton_emb is not None:
                    return self._project_embedding_dimension(triton_emb, source_model="lvface")
            except Exception as exc:
                self._log_triton_fallback("lvface", exc)

        if self._lvface_session is None:
            return None

        try:
            input_w, input_h = self._lvface_input_size
            face = cv2.resize(crop_bgr, (int(input_w), int(input_h)))
            face_rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

            # ViT/ImageNet normalization convention used by most ONNX ViT backbones.
            mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
            std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
            face_norm = (face_rgb - mean) / std

            chw = face_norm.transpose(2, 0, 1)
            batch = np.expand_dims(chw, 0).astype(np.float32)

            input_name = self._lvface_input_name or self._lvface_session.get_inputs()[0].name
            outputs = self._lvface_session.run(None, {input_name: batch})
            if not outputs:
                return None

            emb = np.asarray(outputs[0], dtype=np.float32).flatten()
            return self._project_embedding_dimension(emb, source_model="lvface")
        except Exception as exc:
            logger.debug("LVFace extraction failed: %s", exc)
            return None

    def extract_embedding_lvface(
        self,
        image_bgr: np.ndarray,
        *,
        already_preprocessed: bool = False,
        face_quality_score: float | None = None,
        codeformer_budget_context: dict[str, int] | None = None,
        restoration_mode: str | None = None,
    ) -> np.ndarray | None:
        """Public LVFace extractor with the same preprocessing pipeline used by ArcFace/AdaFace."""
        self.ensure_loaded()
        if self._lvface_session is None:
            return None
        preprocessed = image_bgr if already_preprocessed else self._preprocess(
            image_bgr,
            face_quality_score=face_quality_score,
            codeformer_budget_context=codeformer_budget_context,
            restoration_mode=restoration_mode,
        )
        return self._extract_lvface_embedding(preprocessed)

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

    def _preprocess(
        self,
        image_bgr: np.ndarray,
        *,
        face_quality_score: float | None = None,
        codeformer_budget_context: dict[str, int] | None = None,
        restoration_mode: str | None = None,
    ) -> np.ndarray:
        """P1-1: Apply enhanced preprocessing pipeline."""
        if not settings.enable_preprocessing:
            return image_bgr
        try:
            from backend.services.preprocessing import preprocess_face_crop

            mode = (restoration_mode or "auto").strip().lower()
            runtime_gates = self.get_runtime_gates()

            enable_sr = bool(settings.enable_super_resolution)
            enable_codeformer = bool(runtime_gates.get("enable_codeformer", False))
            allow_sr_after_codeformer = False

            if mode == "none":
                enable_sr = False
                enable_codeformer = False
            elif mode == "realesrgan":
                enable_sr = True
                enable_codeformer = False
            elif mode == "codeformer":
                enable_sr = False
                enable_codeformer = True
            elif mode == "both":
                enable_sr = True
                enable_codeformer = True
                allow_sr_after_codeformer = True

            started = time.perf_counter()
            preprocessed, metadata = preprocess_face_crop(
                image_bgr,
                enable_sr=enable_sr,
                min_upscale_px=settings.min_face_upscale_px,
                sr_func=self._sr_func,
                enable_codeformer=enable_codeformer,
                codeformer_func=self._codeformer_func,
                codeformer_fidelity_weight=float(
                    runtime_gates.get("codeformer_fidelity_weight", settings.codeformer_fidelity_weight)
                ),
                codeformer_min_face_px=int(
                    runtime_gates.get("codeformer_min_face_px", settings.codeformer_min_face_px)
                ),
                codeformer_quality_threshold=float(
                    runtime_gates.get(
                        "codeformer_quality_threshold", settings.codeformer_quality_threshold
                    )
                ),
                codeformer_max_per_frame=int(
                    runtime_gates.get("codeformer_max_per_frame", settings.codeformer_max_per_frame)
                ),
                face_quality_score=face_quality_score,
                codeformer_budget_context=codeformer_budget_context,
                allow_sr_after_codeformer=allow_sr_after_codeformer,
                return_metadata=True,
            )

            if bool(metadata.get("codeformer_attempted", False)):
                self._restoration_stats["codeformer_attempts"] += 1.0
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                self._restoration_stats["codeformer_latency_total_ms"] += float(elapsed_ms)
                self._restoration_stats["codeformer_latency_samples"] += 1.0

            if bool(metadata.get("codeformer_applied", False)):
                self._restoration_stats["codeformer_applied"] += 1.0

                pre_emb = self._extract_arcface_embedding_raw(image_bgr)
                post_emb = self._extract_arcface_embedding_raw(preprocessed)
                threshold = float(
                    runtime_gates.get(
                        "codeformer_identity_preservation_threshold",
                        settings.codeformer_identity_preservation_threshold,
                    )
                )
                if pre_emb is not None and post_emb is not None:
                    similarity = self._cosine_similarity(pre_emb, post_emb)
                    if similarity < threshold:
                        self._restoration_stats["codeformer_discarded"] += 1.0
                        logger.info(
                            "ai_pipeline: discarded CodeFormer restoration similarity=%.4f threshold=%.4f",
                            similarity,
                            threshold,
                        )
                        return image_bgr
                elif pre_emb is None or post_emb is None:
                    logger.debug(
                        "ai_pipeline: CodeFormer safeguard skipped (embedding unavailable)"
                    )

            return preprocessed
        except Exception:
            return image_bgr

    # ── Detection ──────────────────────────────────────────────────

    @staticmethod
    def _prediction_score(pred: Any) -> float:
        score_obj = getattr(pred, "score", None)
        if score_obj is None:
            return 1.0
        if hasattr(score_obj, "value"):
            return float(score_obj.value)
        return float(score_obj)

    def _detect_faces_sahi(
        self,
        image_bgr: np.ndarray,
        *,
        coarse_detector: Any,
        fine_detector: Any,
        runtime_gates: dict,
        enable_dual_pass: bool,
    ) -> list[tuple[int, int, int, int]]:
        if coarse_detector is None:
            return []

        from sahi.predict import get_sliced_prediction

        min_conf = float(runtime_gates["detector_confidence_threshold"])
        nms_iou = float(runtime_gates["detector_nms_iou_threshold"])
        boxes: list[tuple[int, int, int, int]] = []

        result_640 = get_sliced_prediction(
            image_bgr,
            coarse_detector,
            slice_height=640,
            slice_width=640,
            overlap_height_ratio=0.2,
            overlap_width_ratio=0.2,
            postprocess_match_metric="IOU",
            postprocess_match_threshold=nms_iou,
        )
        for pred in result_640.object_prediction_list:
            if self._prediction_score(pred) < min_conf:
                continue
            x1, y1, x2, y2 = pred.bbox.to_xyxy()
            boxes.append((int(x1), int(y1), int(x2 - x1), int(y2 - y1)))

        if enable_dual_pass and fine_detector is not None:
            fine_slice = settings.sahi_fine_slice_size
            fine_overlap = settings.sahi_fine_overlap
            result_fine = get_sliced_prediction(
                image_bgr,
                fine_detector,
                slice_height=fine_slice,
                slice_width=fine_slice,
                overlap_height_ratio=fine_overlap,
                overlap_width_ratio=fine_overlap,
                postprocess_match_metric="IOU",
                postprocess_match_threshold=nms_iou,
            )
            for pred in result_fine.object_prediction_list:
                if self._prediction_score(pred) < min_conf:
                    continue
                x1, y1, x2, y2 = pred.bbox.to_xyxy()
                boxes.append((int(x1), int(y1), int(x2 - x1), int(y2 - y1)))

        if len(boxes) > 1:
            boxes = self._nms_merge(boxes, iou_threshold=nms_iou)
        return boxes

    def _detect_faces_native(
        self,
        image_bgr: np.ndarray,
        *,
        model: Any,
        runtime_gates: dict,
    ) -> list[tuple[int, int, int, int]]:
        if model is None:
            return []

        min_conf = float(runtime_gates["detector_confidence_threshold"])
        nms_iou = float(runtime_gates["detector_nms_iou_threshold"])
        boxes: list[tuple[int, int, int, int]] = []
        results = model.predict(
            source=image_bgr,
            conf=min_conf,
            iou=nms_iou,
            verbose=False,
        )
        for result in results:
            for det in getattr(result, "boxes", []):
                conf = float(det.conf.item()) if hasattr(det, "conf") else 1.0
                if conf < min_conf:
                    continue
                x1, y1, x2, y2 = [float(v) for v in det.xyxy[0].tolist()]
                boxes.append((int(x1), int(y1), int(x2 - x1), int(y2 - y1)))

        if len(boxes) > 1:
            boxes = self._nms_merge(boxes, iou_threshold=nms_iou)
        return boxes

    def detect_faces(
        self,
        image_bgr: np.ndarray,
        detector_mode: str | None = None,
    ) -> list[tuple[int, int, int, int]]:
        """Detect faces using a selected detector backend.

        Supported modes:
        - yolov8_sahi (default/fallback)
        - yolov12_native
        - yolov12_sahi
        - yolo26_reference (benchmark-only, optional model)
        """
        self.ensure_loaded()
        if detector_mode is None and self._triton_available():
            try:
                return self._triton_client.detect_faces(image_bgr)
            except Exception as exc:
                self._log_triton_fallback("yolov12", exc)

        runtime_gates = self.get_runtime_gates()
        mode = (detector_mode or "").strip().lower()

        if not mode:
            if settings.enable_yolov12 and self._detector_yolov12 is not None:
                mode = "yolov12_sahi" if settings.enable_dual_pass_sahi else "yolov12_native"
            else:
                mode = "yolov8_sahi"

        if mode == "yolov12_native":
            if self._yolov12_native_model is None:
                try:
                    from ultralytics import YOLO

                    self._yolov12_native_model = YOLO(settings.yolov12_model_path)
                    logger.info(
                        "ai_pipeline: YOLOv12 native detector loaded on-demand for benchmark mode"
                    )
                except Exception as exc:
                    logger.warning(
                        "ai_pipeline: failed to load YOLOv12 native detector for benchmark mode: %s",
                        exc,
                    )
            if self._yolov12_native_model is not None:
                return self._detect_faces_native(
                    image_bgr,
                    model=self._yolov12_native_model,
                    runtime_gates=runtime_gates,
                )
            logger.debug("ai_pipeline: yolov12_native unavailable, falling back to yolov8_sahi")
            mode = "yolov8_sahi"

        if mode == "yolov12_sahi":
            if self._detector_yolov12 is None:
                try:
                    self._detector_yolov12 = self._load_yolov12_detector()
                    logger.info(
                        "ai_pipeline: SAHI YOLOv12 detector loaded on-demand for benchmark mode"
                    )
                except Exception as exc:
                    logger.warning(
                        "ai_pipeline: failed to load SAHI YOLOv12 detector for benchmark mode: %s",
                        exc,
                    )
            if (
                settings.enable_dual_pass_sahi
                and self._detector_yolov12_fine is None
                and self._detector_yolov12 is not None
            ):
                try:
                    self._detector_yolov12_fine = self._load_sahi_detector(
                        model_path=settings.yolov12_model_path,
                        confidence_threshold=settings.detector_confidence_threshold,
                    )
                except Exception:
                    self._detector_yolov12_fine = None
            if self._detector_yolov12 is not None:
                return self._detect_faces_sahi(
                    image_bgr,
                    coarse_detector=self._detector_yolov12,
                    fine_detector=self._detector_yolov12_fine,
                    runtime_gates=runtime_gates,
                    enable_dual_pass=settings.enable_dual_pass_sahi,
                )
            logger.debug("ai_pipeline: yolov12_sahi unavailable, falling back to yolov8_sahi")
            mode = "yolov8_sahi"

        if mode == "yolo26_reference":
            if self._yolo26_native_model is None:
                try:
                    from ultralytics import YOLO

                    self._yolo26_native_model = YOLO("models/yolo26l.pt")
                    logger.info("ai_pipeline: YOLO26 reference model loaded for benchmark mode")
                except Exception as exc:
                    logger.warning("ai_pipeline: YOLO26 reference model unavailable: %s", exc)
                    return []
            return self._detect_faces_native(
                image_bgr,
                model=self._yolo26_native_model,
                runtime_gates=runtime_gates,
            )

        return self._detect_faces_sahi(
            image_bgr,
            coarse_detector=self._detector_yolov8,
            fine_detector=self._detector_yolov8_fine,
            runtime_gates=runtime_gates,
            enable_dual_pass=settings.enable_dual_pass_sahi,
        )

    def detect_faces_sahi(
        self, image_bgr: np.ndarray
    ) -> list[tuple[int, int, int, int]]:
        """Backward-compatible detector entrypoint used by API routes."""
        return self.detect_faces(image_bgr)

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

    def estimate_pose_label(self, face_bgr: np.ndarray) -> tuple[str, float]:
        """Estimate coarse face pose label from landmarks.

        Returns one of: frontal, left_34, right_34 plus confidence in [0, 1].
        """
        self.ensure_loaded()
        if self._recognizer is None:
            return "frontal", 0.0

        try:
            faces = self._recognizer.get(face_bgr)
            if not faces:
                return "frontal", 0.0

            best_face = max(
                faces,
                key=lambda f: float(
                    (getattr(f, "bbox", [0, 0, 0, 0])[2]
                     - getattr(f, "bbox", [0, 0, 0, 0])[0])
                    * (getattr(f, "bbox", [0, 0, 0, 0])[3]
                       - getattr(f, "bbox", [0, 0, 0, 0])[1])
                ),
            )

            kps = getattr(best_face, "kps", None)
            if kps is None or len(kps) < 3:
                return "frontal", 0.0

            left_eye = np.asarray(kps[0], dtype=np.float32)
            right_eye = np.asarray(kps[1], dtype=np.float32)
            nose = np.asarray(kps[2], dtype=np.float32)

            eye_mid = (left_eye + right_eye) / 2.0
            eye_dist = float(np.linalg.norm(right_eye - left_eye))
            if eye_dist < 1e-6:
                return "frontal", 0.0

            yaw_indicator = float((nose[0] - eye_mid[0]) / eye_dist)
            abs_yaw = abs(yaw_indicator)

            frontal_cutoff = 0.12
            side_cutoff = 0.20
            if abs_yaw < frontal_cutoff:
                confidence = float(max(0.2, 1.0 - (abs_yaw / frontal_cutoff)))
                return "frontal", min(confidence, 1.0)

            confidence = float(min(1.0, abs_yaw / side_cutoff))
            if yaw_indicator > 0:
                return "left_34", max(confidence, 0.4)
            return "right_34", max(confidence, 0.4)
        except Exception:
            return "frontal", 0.0

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
        runtime_gates: dict | None = None,
    ) -> tuple[float, float]:
        """Compute face quality score (0-1) from area ratio + sharpness."""
        gates = runtime_gates or self.get_runtime_gates()
        x, y, w, h = bbox
        frame_h, frame_w = full_image_shape[:2]
        face_area = float(max(w, 1) * max(h, 1))
        frame_area = float(max(frame_w * frame_h, 1))
        area_ratio = self._safe_ratio(face_area, frame_area)
        sharpness = self._face_sharpness(crop_bgr)

        area_score = min(
            1.0, area_ratio / max(float(gates["min_face_area_ratio"]), 1e-8)
        )
        blur_score = min(
            1.0, sharpness / max(float(gates["min_blur_variance"]), 1e-6)
        )
        quality = 0.55 * area_score + 0.45 * blur_score
        return float(quality), float(sharpness)

    def _is_face_usable(
        self,
        bbox: tuple[int, int, int, int],
        quality_score: float,
        sharpness: float,
        runtime_gates: dict | None = None,
    ) -> bool:
        """Check if a detected face passes quality gates."""
        gates = runtime_gates or self.get_runtime_gates()
        _, _, w, h = bbox
        if min(w, h) < int(gates["min_face_size_px"]):
            return False
        if sharpness < float(gates["min_blur_variance"]):
            return False
        if quality_score < float(gates["min_face_quality_score"]):
            return False
        return True

    def _match_decision(
        self,
        best_score: float,
        second_best: float,
        *,
        decision_model: str | None = None,
        runtime_gates: dict | None = None,
    ) -> bool:
        """Two-tier match decision with optional model-specific thresholds."""
        gates = runtime_gates or self.get_runtime_gates()

        strict_threshold = float(gates["face_match_threshold"])
        relaxed_threshold = float(gates["face_match_relaxed_threshold"])
        if decision_model == "lvface":
            strict_threshold = float(gates.get("lvface_match_threshold", strict_threshold))
            relaxed_threshold = float(
                gates.get("lvface_match_relaxed_threshold", relaxed_threshold)
            )

        required_margin = float(gates["face_match_margin"])
        margin_ok = (best_score - second_best) >= required_margin
        return best_score >= strict_threshold or (
            best_score >= relaxed_threshold and margin_ok
        )

    # ── Recognition ────────────────────────────────────────────────

    def _build_template_matrix(
        self,
        db_session,
        *,
        model_name: str,
    ) -> tuple[list[int], np.ndarray, dict[int, list[int]], np.ndarray]:
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
        retention_rows: list[float] = []
        student_rows: dict[int, list[int]] = {}

        # Get enrolled templates for the selected recognition model.
        # Prefer active templates, but retain backup fallback for students
        # that do not yet have active templates.
        from sqlalchemy import select

        query = (
            select(StudentEmbedding)
            .join(Student)
            .where(
                Student.is_enrolled.is_(True),
                StudentEmbedding.model_name == model_name,
                StudentEmbedding.template_status != "quarantined",
            )
        )
        result = db_session.execute(query)
        grouped: dict[int, list[StudentEmbedding]] = {}
        for se in result.scalars().all():
            grouped.setdefault(se.student_id, []).append(se)

        for student_id, embeds in grouped.items():
            active = [
                se
                for se in embeds
                if se.is_active and se.template_status == "active"
            ]
            selected = active if active else embeds
            for se in selected:
                embedding = np.asarray(se.embedding, dtype=np.float32).flatten()
                if (
                    embedding.shape[0] != EMBEDDING_DIMENSION
                    or not np.isfinite(embedding).all()
                ):
                    continue
                idx = len(rows)
                row_student_ids.append(student_id)
                rows.append(self._normalize(embedding))
                retention_rows.append(
                    max(float(se.retention_score or 0.0), 0.0)
                )
                student_rows.setdefault(student_id, []).append(idx)

        matrix = (
            np.vstack(rows)
            if rows
            else np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
        )
        retention = (
            np.asarray(retention_rows, dtype=np.float32)
            if retention_rows
            else np.empty((0,), dtype=np.float32)
        )
        return row_student_ids, matrix, student_rows, retention

    @staticmethod
    def _score_per_student(
        *,
        probe: np.ndarray | None,
        matrix: np.ndarray,
        student_rows: dict[int, list[int]],
        retention_scores: np.ndarray,
    ) -> dict[int, float]:
        """Compute max template score per student for one model."""
        if probe is None or matrix.shape[0] == 0:
            return {}

        cosine_scores = matrix @ probe
        combined_scores = 0.9 * cosine_scores + 0.1 * retention_scores
        per_student: dict[int, float] = {}
        for sid, row_idxs in student_rows.items():
            per_student[sid] = float(max(combined_scores[idx] for idx in row_idxs))
        return per_student

    @staticmethod
    def _ann_search_filters(
        *,
        model_name: str,
        runtime_gates: dict,
    ) -> VectorSearchFilters:
        return VectorSearchFilters(
            model_name=model_name,
            active_only=bool(runtime_gates.get("ann_filter_active_only", False)),
            exclude_quarantined=bool(
                runtime_gates.get("ann_filter_exclude_quarantined", True)
            ),
            enrollment_year=runtime_gates.get("ann_filter_enrollment_year"),
            department=runtime_gates.get("ann_filter_department"),
        )

    @staticmethod
    def _resolve_ann_backend(runtime_gates: dict) -> str:
        backend = str(runtime_gates.get("ann_retrieval_backend", "numpy")).lower()
        if backend not in {"numpy", "hnsw", "diskann"}:
            return "numpy"
        return backend

    def _score_per_student_ann(
        self,
        db_session,
        *,
        probe: np.ndarray | None,
        model_name: str,
        ann_backend: str,
        runtime_gates: dict,
    ) -> dict[int, float]:
        """Compute per-student scores using SQL ANN retrieval backends."""
        if probe is None:
            return {}

        filters = self._ann_search_filters(model_name=model_name, runtime_gates=runtime_gates)
        k = int(runtime_gates.get("ann_search_k", 64))

        query_vector = probe.astype(np.float32).tolist()
        if ann_backend == "diskann":
            rows = find_nearest_faces_diskann_sync(
                db_session,
                query_vector,
                k=k,
                enrolled_only=True,
                filters=filters,
            )
        else:
            rows = find_nearest_faces_sync(
                db_session,
                query_vector,
                k=k,
                enrolled_only=True,
                filters=filters,
            )

        per_student: dict[int, float] = {}
        for row in rows:
            student_id = int(row["student_id"])
            similarity = float(row.get("similarity", 0.0))
            retention = max(float(row.get("retention_score") or 0.0), 0.0)
            combined_score = float(0.9 * similarity + 0.1 * retention)
            current = per_student.get(student_id)
            if current is None or combined_score > current:
                per_student[student_id] = combined_score
        return per_student

    def recognize(
        self,
        db_session,
        image_bgr: np.ndarray,
        schedule_id: int,
        detector_mode: str | None = None,
        restoration_mode: str | None = None,
    ) -> list[FaceMatch]:
        """Full recognition pipeline: detect → quality-check → embed → match.

        Args:
            db_session: SQLAlchemy session (sync for Celery workers).
            image_bgr: Full-frame BGR image.
            schedule_id: Schedule ID for context.
            detector_mode: Optional detector backend override.
            restoration_mode: Optional restoration override for A/B evaluation.

        Returns:
            Deduplicated list of FaceMatch objects.
        """
        self.ensure_loaded()
        runtime_gates = self.get_runtime_gates()
        boxes = self.detect_faces(image_bgr, detector_mode=detector_mode)

        requested_ann_backend = self._resolve_ann_backend(runtime_gates)
        active_ann_backend = requested_ann_backend
        diskann_enabled = bool(runtime_gates.get("enable_diskann", False))

        arcface_matrix = np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
        adaface_matrix = np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
        lvface_matrix = np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)
        arcface_student_rows: dict[int, list[int]] = {}
        adaface_student_rows: dict[int, list[int]] = {}
        lvface_student_rows: dict[int, list[int]] = {}
        arcface_retention_scores = np.empty((0,), dtype=np.float32)
        adaface_retention_scores = np.empty((0,), dtype=np.float32)
        lvface_retention_scores = np.empty((0,), dtype=np.float32)
        numpy_template_banks_loaded = False

        def ensure_numpy_template_banks() -> bool:
            nonlocal arcface_matrix
            nonlocal adaface_matrix
            nonlocal lvface_matrix
            nonlocal arcface_student_rows
            nonlocal adaface_student_rows
            nonlocal lvface_student_rows
            nonlocal arcface_retention_scores
            nonlocal adaface_retention_scores
            nonlocal lvface_retention_scores
            nonlocal numpy_template_banks_loaded

            if numpy_template_banks_loaded:
                return (
                    arcface_matrix.shape[0] > 0
                    or adaface_matrix.shape[0] > 0
                    or lvface_matrix.shape[0] > 0
                )

            _, arcface_matrix, arcface_student_rows, arcface_retention_scores = (
                self._build_template_matrix(db_session, model_name="arcface")
            )
            _, adaface_matrix, adaface_student_rows, adaface_retention_scores = (
                self._build_template_matrix(db_session, model_name="adaface")
            )
            _, lvface_matrix, lvface_student_rows, lvface_retention_scores = (
                self._build_template_matrix(db_session, model_name="lvface")
            )
            numpy_template_banks_loaded = True
            return (
                arcface_matrix.shape[0] > 0
                or adaface_matrix.shape[0] > 0
                or lvface_matrix.shape[0] > 0
            )

        if active_ann_backend == "diskann":
            if not diskann_enabled:
                logger.warning(
                    "ai_pipeline: DiskANN backend requested but enable_diskann is false; falling back to hnsw"
                )
                active_ann_backend = "hnsw"
            else:
                try:
                    if not is_diskann_ready_sync(db_session):
                        logger.warning(
                            "ai_pipeline: DiskANN backend unavailable (extension or index missing); falling back to hnsw"
                        )
                        active_ann_backend = "hnsw"
                except Exception as exc:
                    logger.warning(
                        "ai_pipeline: DiskANN readiness check failed (%s); falling back to hnsw",
                        exc,
                    )
                    active_ann_backend = "hnsw"

        if active_ann_backend == "numpy" and not ensure_numpy_template_banks():
            return []

        def score_with_numpy(
            arcface_probe: np.ndarray | None,
            adaface_probe: np.ndarray | None,
            lvface_probe: np.ndarray | None,
        ) -> dict[str, dict[int, float]]:
            return {
                "arcface": self._score_per_student(
                    probe=arcface_probe,
                    matrix=arcface_matrix,
                    student_rows=arcface_student_rows,
                    retention_scores=arcface_retention_scores,
                ),
                "adaface": self._score_per_student(
                    probe=adaface_probe,
                    matrix=adaface_matrix,
                    student_rows=adaface_student_rows,
                    retention_scores=adaface_retention_scores,
                ),
                "lvface": self._score_per_student(
                    probe=lvface_probe,
                    matrix=lvface_matrix,
                    student_rows=lvface_student_rows,
                    retention_scores=lvface_retention_scores,
                ),
            }

        matches: list[FaceMatch] = []
        fusion_mode = str(runtime_gates.get("recognition_fusion_mode", "weighted_average"))
        forced_model = runtime_gates.get("forced_model")
        codeformer_budget_context = {"used": 0}

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
                runtime_gates=runtime_gates,
            )
            if not self._is_face_usable(
                (x, y, w, h),
                quality_score=quality_score,
                sharpness=sharpness,
                runtime_gates=runtime_gates,
            ):
                continue

            preprocessed_crop = self._preprocess(
                crop,
                face_quality_score=quality_score,
                codeformer_budget_context=codeformer_budget_context,
                restoration_mode=restoration_mode,
            )

            arcface_probe = (
                self.extract_embedding(preprocessed_crop, already_preprocessed=True)
                if active_ann_backend != "numpy" or arcface_matrix.shape[0] > 0
                else None
            )
            adaface_probe = (
                self.extract_embedding_adaface(
                    preprocessed_crop,
                    already_preprocessed=True,
                )
                if active_ann_backend != "numpy" or adaface_matrix.shape[0] > 0
                else None
            )
            lvface_probe = (
                self.extract_embedding_lvface(
                    preprocessed_crop,
                    already_preprocessed=True,
                )
                if active_ann_backend != "numpy" or lvface_matrix.shape[0] > 0
                else None
            )

            if arcface_probe is None and adaface_probe is None and lvface_probe is None:
                continue

            if active_ann_backend == "numpy":
                model_scores = score_with_numpy(
                    arcface_probe,
                    adaface_probe,
                    lvface_probe,
                )
            else:
                try:
                    model_scores = {
                        "arcface": self._score_per_student_ann(
                            db_session,
                            probe=arcface_probe,
                            model_name="arcface",
                            ann_backend=active_ann_backend,
                            runtime_gates=runtime_gates,
                        ),
                        "adaface": self._score_per_student_ann(
                            db_session,
                            probe=adaface_probe,
                            model_name="adaface",
                            ann_backend=active_ann_backend,
                            runtime_gates=runtime_gates,
                        ),
                        "lvface": self._score_per_student_ann(
                            db_session,
                            probe=lvface_probe,
                            model_name="lvface",
                            ann_backend=active_ann_backend,
                            runtime_gates=runtime_gates,
                        ),
                    }
                except Exception as ann_exc:
                    if active_ann_backend == "diskann":
                        logger.warning(
                            "ai_pipeline: DiskANN query failed (%s); falling back to hnsw",
                            ann_exc,
                        )
                        active_ann_backend = "hnsw"
                        try:
                            model_scores = {
                                "arcface": self._score_per_student_ann(
                                    db_session,
                                    probe=arcface_probe,
                                    model_name="arcface",
                                    ann_backend="hnsw",
                                    runtime_gates=runtime_gates,
                                ),
                                "adaface": self._score_per_student_ann(
                                    db_session,
                                    probe=adaface_probe,
                                    model_name="adaface",
                                    ann_backend="hnsw",
                                    runtime_gates=runtime_gates,
                                ),
                                "lvface": self._score_per_student_ann(
                                    db_session,
                                    probe=lvface_probe,
                                    model_name="lvface",
                                    ann_backend="hnsw",
                                    runtime_gates=runtime_gates,
                                ),
                            }
                        except Exception as hnsw_exc:
                            logger.warning(
                                "ai_pipeline: HNSW fallback query failed (%s); falling back to numpy",
                                hnsw_exc,
                            )
                            active_ann_backend = "numpy"
                            if not ensure_numpy_template_banks():
                                continue
                            model_scores = score_with_numpy(
                                arcface_probe,
                                adaface_probe,
                                lvface_probe,
                            )
                    else:
                        logger.warning(
                            "ai_pipeline: HNSW query failed (%s); falling back to numpy",
                            ann_exc,
                        )
                        active_ann_backend = "numpy"
                        if not ensure_numpy_template_banks():
                            continue
                        model_scores = score_with_numpy(
                            arcface_probe,
                            adaface_probe,
                            lvface_probe,
                        )

            available_models = {
                model_name
                for model_name, scores in model_scores.items()
                if bool(scores)
            }
            if not available_models:
                continue

            if isinstance(forced_model, str) and forced_model in available_models:
                selected_models = {forced_model}
            elif fusion_mode in {"arcface_only", "adaface_only", "lvface_only"}:
                requested = fusion_mode.replace("_only", "")
                selected_models = {requested} if requested in available_models else set()
            else:
                selected_models = set(available_models)

            if not selected_models:
                continue

            fusion_weights = self.model_fusion_weights(
                runtime_gates,
                available_models=selected_models,
            )

            best_student_id = None
            best_score = -1.0
            second_best = -1.0
            best_decision_model: str | None = None

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
                elif combined_score > second_best:
                    second_best = combined_score

            if best_student_id is None:
                continue

            if self._match_decision(
                best_score,
                second_best,
                decision_model=best_decision_model,
                runtime_gates=runtime_gates,
            ):
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

    def _triton_available(self) -> bool:
        return bool(settings.enable_triton and self._triton_client and self._triton_client.is_available())

    def _log_triton_fallback(self, model_name: str, exc: Exception) -> None:
        key = f"{model_name}:{type(exc).__name__}:{str(exc)}"
        if key not in self._triton_fallback_logged:
            logger.warning(
                "ai_pipeline: Triton fallback activated model=%s reason=%s",
                model_name,
                exc,
            )
            self._triton_fallback_logged.add(key)
        inference_stats.record_fallback(model_name=model_name, reason=type(exc).__name__)

    def _triton_super_resolve(self, crop_bgr: np.ndarray) -> np.ndarray:
        if not self._triton_available():
            if self._sr_func_local is not None:
                return self._sr_func_local(crop_bgr)
            return crop_bgr

        try:
            return self._triton_client.super_resolve(crop_bgr)
        except Exception as exc:
            self._log_triton_fallback("realesrgan", exc)
            if self._sr_func_local is not None:
                return self._sr_func_local(crop_bgr)
            return crop_bgr

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
        runtime_gates = self.get_runtime_gates()
        active_detector_mode = (
            "yolov12_sahi"
            if settings.enable_yolov12 and self._detector_yolov12 is not None
            else "yolov8_sahi"
        )
        if active_detector_mode == "yolov12_sahi" and not settings.enable_dual_pass_sahi:
            active_detector_mode = "yolov12_native"

        return {
            "recognizer_loaded": self._recognizer is not None,
            "detector_loaded": self._detector is not None,
            "detector_fine_loaded": self._detector_fine is not None,
            "detector_mode_active": active_detector_mode,
            "yolov12_enabled": settings.enable_yolov12,
            "yolov12_sahi_loaded": self._detector_yolov12 is not None,
            "yolov12_native_loaded": self._yolov12_native_model is not None,
            "direct_recognition": self._recognition_model is not None,
            "sahi_available": self._sahi_available,
            "dual_pass_sahi": (
                settings.enable_dual_pass_sahi
                and self._detector_fine is not None
            ),
            "preprocessing_enabled": settings.enable_preprocessing,
            "super_resolution_enabled": settings.enable_super_resolution,
            "sr_func_available": self._sr_func is not None,
            "codeformer_enabled": bool(runtime_gates.get("enable_codeformer", False)),
            "codeformer_available": self._codeformer_func is not None,
            "codeformer_min_face_px": runtime_gates["codeformer_min_face_px"],
            "codeformer_quality_threshold": runtime_gates["codeformer_quality_threshold"],
            "codeformer_max_per_frame": runtime_gates["codeformer_max_per_frame"],
            "codeformer_fidelity_weight": runtime_gates["codeformer_fidelity_weight"],
            "codeformer_identity_preservation_threshold": runtime_gates[
                "codeformer_identity_preservation_threshold"
            ],
            "adaface_available": self._adaface_session is not None,
            "lvface_enabled": settings.enable_lvface,
            "lvface_available": self._lvface_session is not None,
            "lvface_input_size": list(self._lvface_input_size),
            "liveness_enabled": settings.enable_liveness_check,
            "onnx_provider_preference": build_onnx_execution_providers(
                settings.insightface_provider
            ),
            "triton_enabled": settings.enable_triton,
            "triton_available": self._triton_available(),
            "triton_url": settings.triton_url,
            "triton_status": (
                self._triton_client.status() if self._triton_client is not None else {
                    "enabled": settings.enable_triton,
                    "available": False,
                    "url": settings.triton_url,
                    "init_error": "not_initialized",
                }
            ),
            "primary_model": runtime_gates["primary_model"],
            "recognition_fusion_mode": runtime_gates["recognition_fusion_mode"],
            "forced_model": runtime_gates["forced_model"],
            "match_threshold": runtime_gates["face_match_threshold"],
            "match_relaxed_threshold": runtime_gates[
                "face_match_relaxed_threshold"
            ],
            "lvface_match_threshold": runtime_gates["lvface_match_threshold"],
            "lvface_match_relaxed_threshold": runtime_gates[
                "lvface_match_relaxed_threshold"
            ],
            "match_margin": runtime_gates["face_match_margin"],
            "min_face_size_px": runtime_gates["min_face_size_px"],
            "min_blur_variance": runtime_gates["min_blur_variance"],
            "min_face_quality_score": runtime_gates["min_face_quality_score"],
            "detector_confidence_threshold": runtime_gates[
                "detector_confidence_threshold"
            ],
            "detector_nms_iou_threshold": runtime_gates[
                "detector_nms_iou_threshold"
            ],
            "arcface_weight": runtime_gates["arcface_weight"],
            "adaface_weight": runtime_gates["adaface_weight"],
            "lvface_weight": runtime_gates["lvface_weight"],
            "adaface_fusion_weight": runtime_gates["adaface_fusion_weight"],
            "inference_stats": inference_stats.snapshot(),
            "restoration_stats": self.get_restoration_stats(),
        }


# Module-level singleton (lazy — no models loaded until first use)
ai_pipeline = AIPipeline()
