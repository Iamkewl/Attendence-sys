"""Unit tests for core utility modules."""

import numpy as np
import pytest
from backend.core.constants import (
    UserRole,
    PoseLabel,
    EmbeddingModel,
    ErrorCode,
    ATTENDANCE_PRESENT_THRESHOLD,
    EMBEDDING_DIMENSION,
)
from backend.core.config import Settings, build_onnx_execution_providers
from backend.db.vector import VectorSearchFilters, _build_filter_sql
from backend.services.cross_camera import CrossCameraLinker
from backend.services.fairness_audit import FairnessAuditor
from backend.services.hmac_auth import compute_payload_digest
from backend.services.liveness import check_liveness_flash, check_liveness_rppg
from backend.services.preprocessing import preprocess_face_crop
from backend.services.tracker import TrackerManager
from backend.api.v1.students import _select_enrollment_face_box
from backend.api.v1.websocket import AttendanceBroadcaster
from backend.services.ai_pipeline import FaceMatch, ai_pipeline


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
        assert EmbeddingModel.LVFACE == "lvface"

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


class TestEnrollmentFaceSelection:
    """Validate enrollment-specific single-face selection heuristics."""

    def test_select_single_face_box_when_only_one(self):
        box, warning = _select_enrollment_face_box(
            (720, 1280, 3), [(300, 180, 260, 300)]
        )
        assert box == (300, 180, 260, 300)
        assert warning is None

    def test_dedup_overlapping_boxes(self):
        box, warning = _select_enrollment_face_box(
            (720, 1280, 3),
            [(300, 180, 260, 300), (308, 186, 255, 295)],
        )
        assert box == (300, 180, 260, 300)
        assert warning is None

    def test_accept_dominant_face_with_tiny_artifacts(self):
        box, warning = _select_enrollment_face_box(
            (720, 1280, 3),
            [(280, 160, 280, 320), (1000, 640, 28, 24), (60, 90, 24, 20)],
        )
        assert box == (280, 160, 280, 320)
        assert warning is not None

    def test_reject_two_real_faces(self):
        box, warning = _select_enrollment_face_box(
            (720, 1280, 3),
            [(250, 170, 240, 290), (700, 180, 220, 270)],
        )
        assert box is None
        assert warning is None


class TestOnnxProviderConfiguration:
    """Validate ONNX provider fallback configuration behavior."""

    def test_build_onnx_providers_cpu_fallback(self):
        providers = build_onnx_execution_providers("CUDAExecutionProvider")
        assert providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]

    def test_build_onnx_providers_dedup_cpu(self):
        providers = build_onnx_execution_providers("CPUExecutionProvider")
        assert providers == ["CPUExecutionProvider"]

    def test_settings_default_provider_is_cuda(self):
        settings = Settings(_env_file=None)
        assert settings.insightface_provider == "CUDAExecutionProvider"

    def test_phase1_detector_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_yolov12 is False
        assert settings.yolov12_model_path == "models/yolov12l.pt"
        assert settings.detector_nms_iou_threshold == pytest.approx(0.5)
        assert settings.detector_confidence_threshold == pytest.approx(0.3)

    def test_phase4_tracking_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_tracking is False
        assert settings.tracking_top_n_frames == 5
        assert settings.tracking_consistent_match_count == 3
        assert settings.tracking_max_age_seconds == 300

    def test_phase45_cross_camera_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_cross_camera_reid is False
        assert settings.reid_model_path == "models/osnet_x1_0.onnx"
        assert settings.reid_embedding_dim == 512
        assert settings.cross_camera_time_window_seconds == 300

    def test_phase5_codeformer_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_codeformer is False
        assert settings.codeformer_model_path == "models/codeformer_v0.1.0.onnx"
        assert settings.codeformer_fidelity_weight == pytest.approx(0.7)
        assert settings.codeformer_min_face_px == 40
        assert settings.codeformer_quality_threshold == pytest.approx(0.15)
        assert settings.codeformer_max_per_frame == 2
        assert settings.codeformer_identity_preservation_threshold == pytest.approx(0.8)

    def test_phase6_ann_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_diskann is False
        assert settings.ann_retrieval_backend == "numpy"
        assert settings.ann_search_k == 64
        assert settings.ann_filter_active_only is False
        assert settings.ann_filter_exclude_quarantined is True
        assert settings.ann_filter_enrollment_year is None
        assert settings.ann_filter_department is None

    def test_phase7_liveness_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_rppg_liveness is False
        assert settings.rppg_min_frames == 30
        assert settings.rppg_signal_threshold == pytest.approx(0.3)
        assert settings.enable_flash_liveness is False
        assert settings.flash_scattering_threshold == pytest.approx(0.5)

    def test_phase8_governance_defaults(self):
        settings = Settings(_env_file=None)
        assert settings.enable_auto_template_refresh is False
        assert settings.auto_refresh_min_confidence == pytest.approx(0.98)
        assert settings.auto_refresh_min_quality == pytest.approx(0.50)
        assert settings.auto_refresh_max_age_days == 180
        assert settings.enable_fairness_audit is False
        assert settings.enable_data_retention is False
        assert settings.enable_camera_drift_detection is False


class TestFairnessAuditing:
    def test_generate_report_contains_disparity_ratios(self):
        auditor = FairnessAuditor(min_group_samples=1)
        records = [
            {
                "expected_student_id": 1,
                "predicted_student_id": 1,
                "confidence": 0.99,
                "department": "CS",
                "enrollment_year": "2024",
                "self_reported_category": "A",
            },
            {
                "expected_student_id": 2,
                "predicted_student_id": None,
                "confidence": 0.10,
                "department": "CS",
                "enrollment_year": "2024",
                "self_reported_category": "A",
            },
            {
                "expected_student_id": 3,
                "predicted_student_id": 3,
                "confidence": 0.97,
                "department": "EE",
                "enrollment_year": "2023",
                "self_reported_category": "B",
            },
        ]

        report = auditor.generate_report(records, generated_by="unit-test")

        assert report["record_count"] == 3
        assert "overall" in report
        assert "groups" in report
        assert "disparity_ratios" in report
        assert "department" in report["groups"]
        assert report["groups"]["department"]["CS"]["sample_count"] == 2


class TestLivenessTierExtensions:
    def test_rppg_detects_periodic_signal(self):
        fps = 30.0
        frames: list[np.ndarray] = []
        bboxes: list[tuple[int, int, int, int]] = []

        for i in range(40):
            frame = np.full((120, 120, 3), 70, dtype=np.uint8)
            pulse = int(8.0 * np.sin(2.0 * np.pi * 1.2 * (i / fps)))
            frame[20:90, 20:90, 1] = np.clip(120 + pulse, 0, 255)
            frame[20:90, 20:90, 2] = np.clip(105 + int(2.0 * np.sin(2.0 * np.pi * 1.2 * (i / fps))), 0, 255)
            frames.append(frame)
            bboxes.append((20, 20, 70, 70))

        is_live, estimated_hr, quality = check_liveness_rppg(
            frames,
            bboxes,
            fps=fps,
            min_frames=30,
            signal_threshold=0.3,
        )
        assert is_live is True
        assert 42.0 <= estimated_hr <= 240.0
        assert quality >= 0.3

    def test_flash_scattering_rejects_uniform_response(self):
        pre = np.full((100, 100, 3), 90, dtype=np.uint8)
        post = np.full((100, 100, 3), 120, dtype=np.uint8)

        is_live, score, pattern = check_liveness_flash(
            pre,
            post,
            threshold=0.5,
            face_bbox=(20, 20, 60, 60),
        )
        assert is_live is False
        assert 0.0 <= score <= 1.0
        assert isinstance(pattern, str)

    def test_flash_scattering_accepts_skin_like_edge_response(self):
        pre = np.full((120, 120, 3), 70, dtype=np.uint8)
        pre[42:52, 38:50, :] = 96
        pre[42:52, 70:82, :] = 94
        pre[70:76, 46:74, :] = 100
        post = pre.copy()

        x1, y1, x2, y2 = 20, 20, 100, 100
        cy = (y1 + y2) / 2.0
        cx = (x1 + x2) / 2.0
        max_dist = np.hypot((x2 - x1) / 2.0, (y2 - y1) / 2.0)

        for y in range(y1, y2):
            for x in range(x1, x2):
                dist = np.hypot(x - cx, y - cy) / max_dist
                edge_boost = int(18 + 24 * dist)
                texture = 6 if (x + y) % 4 == 0 else 0
                post[y, x, 2] = np.clip(int(pre[y, x, 2]) + edge_boost + texture, 0, 255)
                post[y, x, 1] = np.clip(int(pre[y, x, 1]) + 10, 0, 255)

        is_live, score, pattern = check_liveness_flash(
            pre,
            post,
            threshold=0.2,
            face_bbox=(20, 20, 80, 80),
        )
        assert is_live is True
        assert score >= 0.2
        assert pattern == "skin_like_scattering_pattern"


class TestVectorSearchFilters:
    def test_build_filter_sql_for_active_template_and_cohort(self):
        where_sql, _async_params, sync_params, _idx = _build_filter_sql(
            enrolled_only=True,
            filters=VectorSearchFilters(
                active_only=True,
                exclude_quarantined=True,
                enrollment_year=2026,
                department="CS",
                model_name="adaface",
            ),
            async_mode=False,
            query_param_idx=0,
        )

        assert "s.is_enrolled = true" in where_sql
        assert "se.template_status = 'active'" in where_sql
        assert "se.is_active = true" in where_sql
        assert "s.enrollment_year = :enrollment_year" in where_sql
        assert "s.department = :department" in where_sql
        assert "se.model_name = :model_name" in where_sql
        assert sync_params["enrollment_year"] == 2026
        assert sync_params["department"] == "CS"
        assert sync_params["model_name"] == "adaface"

    def test_build_filter_sql_uses_quarantine_exclusion_when_not_active_only(self):
        where_sql, _async_params, sync_params, _idx = _build_filter_sql(
            enrolled_only=False,
            filters=VectorSearchFilters(active_only=False, exclude_quarantined=True),
            async_mode=False,
            query_param_idx=0,
        )

        assert "template_status != 'quarantined'" in where_sql
        assert "template_status = 'active'" not in where_sql
        assert sync_params == {}


class TestAIPipelineScoringDecision:
    def test_score_per_student_uses_max_template_score_per_identity(self):
        probe = np.asarray([1.0, 0.0], dtype=np.float32)
        matrix = np.asarray(
            [
                [1.0, 0.0],
                [0.8, 0.2],
                [0.5, 0.5],
            ],
            dtype=np.float32,
        )
        retention = np.asarray([0.0, 0.4, 0.1], dtype=np.float32)
        student_rows = {101: [0, 1], 202: [2]}

        scores = ai_pipeline._score_per_student(
            probe=probe,
            matrix=matrix,
            student_rows=student_rows,
            retention_scores=retention,
        )

        assert set(scores.keys()) == {101, 202}
        assert scores[101] > scores[202]
        assert scores[101] == pytest.approx(0.9)
        assert scores[202] == pytest.approx(0.46)

    def test_match_decision_honors_strict_relaxed_margin_and_lvface_thresholds(self):
        runtime_gates = {
            "face_match_threshold": 0.85,
            "face_match_relaxed_threshold": 0.78,
            "face_match_margin": 0.08,
            "lvface_match_threshold": 0.82,
            "lvface_match_relaxed_threshold": 0.75,
        }

        assert ai_pipeline._match_decision(0.86, 0.84, runtime_gates=runtime_gates) is True
        assert ai_pipeline._match_decision(0.79, 0.74, runtime_gates=runtime_gates) is False
        assert ai_pipeline._match_decision(0.79, 0.70, runtime_gates=runtime_gates) is True
        assert ai_pipeline._match_decision(
            0.76,
            0.70,
            decision_model="lvface",
            runtime_gates=runtime_gates,
        ) is False
        assert ai_pipeline._match_decision(
            0.83,
            0.80,
            decision_model="lvface",
            runtime_gates=runtime_gates,
        ) is True

    def test_dedupe_by_student_keeps_highest_confidence_match(self):
        matches = [
            FaceMatch(student_id=7, confidence=0.81, bbox=(0, 0, 10, 10), quality=0.9),
            FaceMatch(student_id=7, confidence=0.93, bbox=(2, 2, 11, 11), quality=0.95),
            FaceMatch(student_id=9, confidence=0.88, bbox=(5, 5, 12, 12), quality=0.92),
        ]

        deduped = ai_pipeline._dedupe_by_student(matches)
        by_student = {item.student_id: item for item in deduped}

        assert len(deduped) == 2
        assert by_student[7].confidence == pytest.approx(0.93)
        assert by_student[9].confidence == pytest.approx(0.88)


class TestPreprocessingPolicy:
    def test_codeformer_policy_prefers_codeformer_for_tiny_low_quality(self):
        image = np.full((32, 32, 3), 96, dtype=np.uint8)
        calls = {"codeformer": 0, "sr": 0}
        budget = {"used": 0}

        def _codeformer(crop: np.ndarray, _weight: float) -> np.ndarray:
            calls["codeformer"] += 1
            return np.clip(crop.astype(np.int16) + 5, 0, 255).astype(np.uint8)

        def _sr(crop: np.ndarray) -> np.ndarray:
            calls["sr"] += 1
            return crop

        output, metadata = preprocess_face_crop(
            image,
            enable_sr=True,
            min_upscale_px=64,
            sr_func=_sr,
            enable_codeformer=True,
            codeformer_func=_codeformer,
            codeformer_fidelity_weight=0.7,
            codeformer_min_face_px=40,
            codeformer_quality_threshold=0.15,
            codeformer_max_per_frame=2,
            face_quality_score=0.10,
            codeformer_budget_context=budget,
            return_metadata=True,
        )

        assert calls["codeformer"] == 1
        assert calls["sr"] == 0
        assert budget["used"] == 1
        assert metadata["codeformer_applied"] is True
        assert metadata["restoration_path"] == "codeformer"
        assert output.shape == image.shape

    def test_codeformer_budget_exhausted_falls_back_to_sr(self):
        image = np.full((32, 32, 3), 96, dtype=np.uint8)
        calls = {"codeformer": 0, "sr": 0}
        budget = {"used": 2}

        def _codeformer(crop: np.ndarray, _weight: float) -> np.ndarray:
            calls["codeformer"] += 1
            return crop

        def _sr(crop: np.ndarray) -> np.ndarray:
            calls["sr"] += 1
            return crop

        _output, metadata = preprocess_face_crop(
            image,
            enable_sr=True,
            min_upscale_px=64,
            sr_func=_sr,
            enable_codeformer=True,
            codeformer_func=_codeformer,
            codeformer_fidelity_weight=0.7,
            codeformer_min_face_px=40,
            codeformer_quality_threshold=0.15,
            codeformer_max_per_frame=2,
            face_quality_score=0.10,
            codeformer_budget_context=budget,
            return_metadata=True,
        )

        assert calls["codeformer"] == 0
        assert calls["sr"] == 1
        assert metadata["codeformer_skipped_reason"] == "frame_budget_exhausted"
        assert metadata["restoration_path"] == "realesrgan"


class _FakeTracker:
    """Deterministic tracker stub for unit-testing TrackerManager logic."""

    def __init__(self, outputs: list[np.ndarray]):
        self._outputs = outputs

    def update(self, detections_xyxy: np.ndarray, frame_bgr: np.ndarray) -> np.ndarray:
        if not self._outputs:
            return np.empty((0, 8), dtype=np.float32)
        return self._outputs.pop(0)


class TestTrackerManager:
    def test_track_confirmation_and_reid_trigger(self):
        outputs = [
            np.asarray([[10, 10, 70, 70, 1, 0.95, 0, 0]], dtype=np.float32),
            np.asarray([[12, 12, 72, 72, 1, 0.93, 0, 0]], dtype=np.float32),
            np.asarray([[14, 14, 74, 74, 1, 0.94, 0, 0]], dtype=np.float32),
            np.asarray([[16, 16, 76, 76, 1, 0.92, 0, 0]], dtype=np.float32),
        ]
        fake_tracker = _FakeTracker(outputs)

        quality_values = iter([(0.95, 120.0), (0.94, 118.0), (0.96, 125.0), (0.40, 55.0)])

        def _quality(_crop, _bbox, _shape):
            return next(quality_values)

        manager = TrackerManager(
            quality_fn=_quality,
            top_n_frames=3,
            max_lost_frames=3,
            consistent_match_count=3,
            quality_drop_ratio=0.6,
            tracker_factory=lambda: fake_tracker,
        )

        frame = np.zeros((120, 120, 3), dtype=np.uint8)
        for _ in range(3):
            tracks = manager.update("cam-1", [(10, 10, 60, 60)], frame)
            assert len(tracks) == 1
            track = tracks[0]
            track.record_identity(
                student_id=42,
                confidence=0.93,
                consistent_required=3,
                embedding=np.ones((512,), dtype=np.float32),
            )

        assert track.status == "confirmed"
        assert track.identity == 42
        assert track.needs_identification(consistent_required=3, quality_drop_ratio=0.6) is False

        tracks = manager.update("cam-1", [(10, 10, 60, 60)], frame)
        assert len(tracks) == 1
        track = tracks[0]
        assert track.needs_identification(consistent_required=3, quality_drop_ratio=0.6) is True

    def test_cleanup_stale_removes_tracks(self):
        outputs = [np.asarray([[10, 10, 70, 70, 7, 0.95, 0, 0]], dtype=np.float32)]
        fake_tracker = _FakeTracker(outputs)
        manager = TrackerManager(
            tracker_factory=lambda: fake_tracker,
            quality_fn=lambda _crop, _bbox, _shape: (0.95, 100.0),
        )

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        tracks = manager.update("cam-x", [(10, 10, 60, 60)], frame)
        assert len(tracks) == 1
        tracks[0].last_seen_timestamp -= 999.0

        removed = manager.cleanup_stale(max_age_seconds=1)
        assert removed == 1
        assert manager.get_active_tracks("cam-x") == []


class _FakeTrack:
    def __init__(
        self,
        *,
        track_id: int,
        identity: int | None,
        embedding: np.ndarray | None,
        confidence: float = 0.0,
    ):
        self.track_id = track_id
        self.identity = identity
        self.best_person_embedding = embedding
        self.confidence = confidence


class TestCrossCameraLinker:
    def test_linker_accepts_high_similarity(self):
        linker = CrossCameraLinker()

        src_track = _FakeTrack(
            track_id=10,
            identity=77,
            embedding=np.ones((512,), dtype=np.float32),
            confidence=0.95,
        )
        linker.register_confirmed_track("cam-a", src_track)

        dst_track = _FakeTrack(
            track_id=11,
            identity=None,
            embedding=np.ones((512,), dtype=np.float32),
            confidence=0.0,
        )

        linked = linker.try_link_track("cam-b", dst_track)
        assert linked is not None
        assert int(linked["student_id"]) == 77
        assert int(linked["source_track_id"]) == 10

        metrics = linker.metrics_snapshot()
        assert int(metrics["link_count"]) >= 1


class _FakeWebSocket:
    def __init__(self, should_fail_send: bool = False):
        self.accepted = False
        self.messages: list[str] = []
        self.should_fail_send = should_fail_send

    async def accept(self):
        self.accepted = True

    async def send_text(self, message: str):
        if self.should_fail_send:
            raise RuntimeError("simulated send failure")
        self.messages.append(message)


@pytest.mark.asyncio
class TestAttendanceBroadcaster:
    async def test_subscribe_broadcast_and_unsubscribe(self):
        broadcaster = AttendanceBroadcaster()
        ws_1 = _FakeWebSocket()
        ws_2 = _FakeWebSocket()

        await broadcaster.subscribe(101, ws_1)
        await broadcaster.subscribe(101, ws_2)
        assert ws_1.accepted is True
        assert ws_2.accepted is True
        assert broadcaster.subscriber_count == 2

        sent = await broadcaster.broadcast(101, {"type": "detection", "student_id": 7})
        assert sent == 2
        assert len(ws_1.messages) == 1
        assert len(ws_2.messages) == 1

        broadcaster.unsubscribe(101, ws_1)
        assert broadcaster.subscriber_count == 1

    async def test_broadcast_prunes_dead_subscribers(self):
        broadcaster = AttendanceBroadcaster()
        ws_ok = _FakeWebSocket()
        ws_dead = _FakeWebSocket(should_fail_send=True)

        await broadcaster.subscribe(202, ws_ok)
        await broadcaster.subscribe(202, ws_dead)
        assert broadcaster.subscriber_count == 2

        sent = await broadcaster.broadcast(202, {"type": "snapshot_complete", "snapshot_id": 1})
        assert sent == 1
        assert broadcaster.subscriber_count == 1
        assert len(ws_ok.messages) == 1
