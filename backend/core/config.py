"""Application configuration using Pydantic Settings.

All values are configurable via environment variables or .env file.
Feature flags allow graceful degradation of AI pipeline components.
"""

from functools import lru_cache

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
    insightface_provider: str = "CPUExecutionProvider"
    insightface_det_size: int = 960
    recognition_backend: str = "insightface"
    yolo_model_path: str = "yolov8n-face.pt"
    yolo_model_path_fine: str = "models/yolov8m-face.pt"

    # ── Face Matching ─────────────────────────────────────
    face_match_threshold: float = 0.85
    face_match_relaxed_threshold: float = 0.78
    face_match_margin: float = 0.08
    min_face_size_px: int = 48
    min_face_area_ratio: float = 0.0025
    min_blur_variance: float = 45.0
    min_face_quality_score: float = 0.18

    # ── Burst Capture ─────────────────────────────────────
    burst_capture_count: int = 8
    burst_capture_gap_ms: int = 120
    max_debug_images: int = 5

    # ── Feature Flags: SAHI ───────────────────────────────
    enable_dual_pass_sahi: bool = True
    sahi_fine_slice_size: int = 320
    sahi_fine_overlap: float = 0.3

    # ── Feature Flags: Multi-Pose & AdaFace ───────────────
    enable_multi_pose_enrollment: bool = True
    enable_adaface: bool = True
    adaface_model_path: str = "models/adaface_ir101_webface12m.onnx"

    # ── Feature Flags: Preprocessing & Super-Resolution ───
    enable_preprocessing: bool = True
    enable_super_resolution: bool = True
    super_resolution_model_path: str = "models/realesrgan_x4.onnx"
    min_face_upscale_px: int = 64

    # ── Feature Flags: Liveness ───────────────────────────
    enable_liveness_check: bool = True
    liveness_motion_threshold: float = 2.5
    liveness_flow_min_magnitude: float = 0.8
    liveness_spoof_threshold: float = 0.5
    liveness_anti_spoof_model_path: str = "models/anti_spoof_2.7_80x80.onnx"

    # ── Rate Limiting ─────────────────────────────────────
    ingest_rate_limit_seconds: int = 30

    # ── Login Rate Limiting ───────────────────────────────
    login_max_attempts: int = 5
    login_window_seconds: int = 900  # 15 minutes
    login_lockout_attempts: int = 10


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
