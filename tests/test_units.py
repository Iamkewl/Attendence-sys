"""Unit tests for core utility modules."""

import pytest
from backend.core.constants import (
    UserRole,
    PoseLabel,
    EmbeddingModel,
    ErrorCode,
    ATTENDANCE_PRESENT_THRESHOLD,
    EMBEDDING_DIMENSION,
)
from backend.services.hmac_auth import compute_payload_digest


class TestConstants:
    """Validate constants and enums are correctly defined."""

    def test_user_roles(self):
        assert UserRole.ADMIN == "admin"
        assert UserRole.INSTRUCTOR == "instructor"
        assert UserRole.STUDENT == "student"
        assert UserRole.DEVICE == "device"

    def test_pose_labels(self):
        assert PoseLabel.FRONTAL == "frontal"
        assert PoseLabel.LEFT_34 == "left_34"
        assert PoseLabel.RIGHT_34 == "right_34"

    def test_embedding_models(self):
        assert EmbeddingModel.ARCFACE == "arcface"
        assert EmbeddingModel.ADAFACE == "adaface"

    def test_thresholds(self):
        assert 0.0 < ATTENDANCE_PRESENT_THRESHOLD < 1.0
        assert EMBEDDING_DIMENSION == 512

    def test_error_codes(self):
        assert ErrorCode.HMAC_INVALID == "HMAC_INVALID"
        assert ErrorCode.NONCE_REPLAYED == "NONCE_REPLAYED"
        assert ErrorCode.MODEL_NOT_READY == "MODEL_NOT_READY"


class TestHMACAuth:
    """Test HMAC digest computation."""

    def test_payload_digest_deterministic(self):
        """Same inputs produce same digest."""
        d1 = compute_payload_digest(b"test-image-data", 1, "2024-01-01T00:00:00Z")
        d2 = compute_payload_digest(b"test-image-data", 1, "2024-01-01T00:00:00Z")
        assert d1 == d2

    def test_payload_digest_changes_with_data(self):
        """Different image data produces different digest."""
        d1 = compute_payload_digest(b"image-a", 1, "2024-01-01T00:00:00Z")
        d2 = compute_payload_digest(b"image-b", 1, "2024-01-01T00:00:00Z")
        assert d1 != d2

    def test_payload_digest_changes_with_device(self):
        """Different device_id produces different digest."""
        d1 = compute_payload_digest(b"same", 1, "2024-01-01T00:00:00Z")
        d2 = compute_payload_digest(b"same", 2, "2024-01-01T00:00:00Z")
        assert d1 != d2
