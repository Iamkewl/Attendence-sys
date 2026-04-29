"""Application configuration using Pydantic Settings.

All values are configurable via environment variables or .env file.
Feature flags allow graceful degradation of AI pipeline components.
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Attendance System V2."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Core ──────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/attendance"
    redis_url: str = "redis://localhost:6379/0"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    app_env: str = "development"

    # ── Auth (JWT + Argon2id) ─────────────────────────────
    jwt_secret_key: str = "CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    # ── Celery ────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ── HMAC (Device Auth) ────────────────────────────────
    hmac_algo: str = "sha256"
    nonce_ttl_seconds: int = 60

    # ── Scheduler ─────────────────────────────────────────
    snapshot_interval_minutes: int = 10

    # ── AI Pipeline ───────────────────────────────────────
    insightface_provider: str = "CUDAExecutionProvider"
    insightface_det_size: int = 960
    recognition_backend: str = "insightface"
    yolo_model_path: str = "yolov8n-face.pt"
    yolo_model_path_fine: str = "models/yolov8m-face.pt"
    enable_yolov12: bool = False
    yolov12_model_path: str = "models/yolov12l.pt"
    detector_nms_iou_threshold: float = 0.5
    detector_confidence_threshold: float = 0.3

    # ── Face Matching ─────────────────────────────────────
    face_match_threshold: float = 0.85
    face_match_relaxed_threshold: float = 0.78
    face_match_margin: float = 0.08
    enable_diskann: bool = False
    ann_retrieval_backend: str = "numpy"
    ann_search_k: int = 64
    ann_filter_active_only: bool = False
    ann_filter_exclude_quarantined: bool = True
    ann_filter_enrollment_year: int | None = None
    ann_filter_department: str | None = None
    recognition_fusion_mode: str = "weighted_average"
    arcface_weight: float = 0.35
    adaface_weight: float = 0.30
    lvface_weight: float = 0.35
    lvface_match_threshold: float = 0.85
    lvface_match_relaxed_threshold: float = 0.78
    min_face_size_px: int = 48
    min_face_area_ratio: float = 0.0025
    min_blur_variance: float = 45.0
    min_face_quality_score: float = 0.18
    enrollment_duplicate_similarity_threshold: float = 0.995
    enrollment_collision_similarity_threshold: float = 0.93
    active_templates_per_bucket: int = 5
    backup_templates_per_bucket: int = 10
    enrollment_min_frontal_embeddings: int = 3
    enrollment_min_left_34_embeddings: int = 1
    enrollment_min_right_34_embeddings: int = 1

    # ── Burst Capture ─────────────────────────────────────
    burst_capture_count: int = 8
    burst_capture_gap_ms: int = 120
    max_debug_images: int = 5

    # ── Feature Flags: Temporal Tracking ─────────────────
    enable_tracking: bool = False
    tracking_top_n_frames: int = 5
    tracking_consistent_match_count: int = 3
    tracking_quality_drop_ratio: float = 0.6
    tracking_max_lost_frames: int = 20
    tracking_max_age_seconds: int = 300

    # ── Feature Flags: Cross-Camera ReID ─────────────────
    enable_cross_camera_reid: bool = False
    reid_model_path: str = "models/osnet_x1_0.onnx"
    reid_embedding_dim: int = 512
    enable_person_box_estimation: bool = True
    person_box_height_factor: float = 7.0
    cross_camera_time_window_seconds: int = 300
    cross_camera_face_weight: float = 0.15
    cross_camera_reid_weight: float = 0.75
    cross_camera_time_weight: float = 0.10
    cross_camera_link_threshold: float = 0.72
    camera_transition_priors_path: str = "backend/data/camera_transition_priors.json"

    # ── Feature Flags: SAHI ───────────────────────────────
    enable_dual_pass_sahi: bool = True
    sahi_fine_slice_size: int = 320
    sahi_fine_overlap: float = 0.3

    # ── Feature Flags: Multi-Pose & AdaFace ───────────────
    enable_adaface: bool = True
    adaface_model_path: str = "models/adaface_ir101_webface12m.onnx"
    enable_lvface: bool = False
    lvface_model_path: str = "models/lvface_base.onnx"

    # ── Feature Flags: Governance / Compliance ─────────
    enable_auto_template_refresh: bool = False
    auto_refresh_min_confidence: float = 0.98
    auto_refresh_min_quality: float = 0.50
    auto_refresh_max_age_days: int = 180
    auto_refresh_similarity_threshold: float = 0.95
    auto_refresh_require_liveness: bool = True  # When True, snapshot-only paths skip refresh (no liveness evidence); clip paths with liveness pass are allowed

    enable_fairness_audit: bool = False
    fairness_audit_day_of_month: int = 1
    fairness_min_group_samples: int = 5
    fairness_audit_dataset_path: str = "backend/data/baseline/fairness_dataset.jsonl"
    fairness_audit_output_dir: str = "backend/data/audits"

    enable_data_retention: bool = False
    data_retention_default_program_years: int = 4
    data_retention_grace_years: int = 1
    detection_retention_days: int = 365
    retention_nightly_hour_utc: int = 2

    enable_camera_drift_detection: bool = False
    drift_window_days: int = 7
    drift_drop_threshold: float = 0.20
    drift_min_baseline_days: int = 3

    # ── Feature Flags: Triton Inference Server ──────────
    enable_triton: bool = False
    triton_url: str = "localhost:8001"
    triton_request_timeout_ms: int = 120
    triton_batch_window_ms: int = 4
    triton_max_batch_size: int = 8

    # ── Feature Flags: Preprocessing & Super-Resolution ───
    enable_preprocessing: bool = True
    enable_super_resolution: bool = True
    super_resolution_model_path: str = "models/realesrgan_x4.onnx"
    min_face_upscale_px: int = 64
    enable_codeformer: bool = False
    codeformer_model_path: str = "models/codeformer_v0.1.0.onnx"
    codeformer_fidelity_weight: float = 0.7
    codeformer_min_face_px: int = 40
    codeformer_quality_threshold: float = 0.15
    codeformer_max_per_frame: int = 2
    codeformer_identity_preservation_threshold: float = 0.80

    # ── Feature Flags: Liveness ───────────────────────────
    enable_liveness_check: bool = True
    liveness_motion_threshold: float = 2.5
    liveness_flow_min_magnitude: float = 0.8
    liveness_spoof_threshold: float = 0.5
    liveness_anti_spoof_model_path: str = "models/anti_spoof_2.7_80x80.onnx"
    enable_rppg_liveness: bool = False
    rppg_min_frames: int = 30
    rppg_signal_threshold: float = 0.3
    enable_flash_liveness: bool = False
    flash_scattering_threshold: float = 0.5

    # ── Rate Limiting ─────────────────────────────────────
    ingest_rate_limit_seconds: int = 30

    # ── Login Rate Limiting ───────────────────────────────
    login_max_attempts: int = 5
    login_window_seconds: int = 900  # 15 minutes
    login_lockout_attempts: int = 10

    @model_validator(mode="after")
    def validate_security_settings(self):
        """Guard against weak JWT configuration outside local development."""
        non_prod_envs = {"dev", "development", "local", "test", "testing"}
        if self.app_env.lower() in non_prod_envs:
            return self

        weak_secret_markers = {
            "CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING",
            "dev-super-secret-change-me",
        }
        if self.jwt_secret_key in weak_secret_markers or len(self.jwt_secret_key) < 32:
            raise ValueError(
                "JWT secret key is weak or placeholder in non-development environment"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


def build_onnx_execution_providers(primary_provider: str | None) -> list[str]:
    """Build ONNX Runtime provider chain with guaranteed CPU fallback."""
    providers: list[str] = []
    if primary_provider:
        normalized = primary_provider.strip()
        if normalized:
            providers.append(normalized)

    if "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")
    return providers
