"""Integration tests for student and attendance endpoints."""

from types import SimpleNamespace
from datetime import time
import time as pytime
import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect
from uuid import uuid4

from backend.core.security import create_access_token, hash_password
from backend.models.student import Student
from backend.models.attendance import Detection, Snapshot
from backend.models.course import Course, Schedule
from backend.models.room import Device, Room
from backend.models.user import User
from backend.services.hmac_auth import (
    compute_payload_digest,
    hash_device_secret,
    sign_payload,
)


class _FakeSSERequest:
    async def is_disconnected(self) -> bool:
        return False


class _FakeAttendanceSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.closed: list[tuple[int, str | None]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int, reason: str | None = None) -> None:
        self.closed.append((code, reason))

    async def receive_text(self) -> str:
        raise WebSocketDisconnect(code=1000)


async def _get_admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    """Helper: seed admin user in DB and return access token."""
    email = f"admin-{uuid4().hex[:8]}@test.io"
    admin_user = User(
        email=email,
        password_hash=hash_password("TestAdmin123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin_user)
    await db_session.flush()

    login_res = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "TestAdmin123!",
    })
    return login_res.json().get("access_token", "")


@pytest.mark.asyncio
class TestStudentsAPI:
    """Test student CRUD endpoints."""

    async def test_list_students_empty(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/v1/students — returns empty list initially."""
        token = await _get_admin_token(client, db_session)
        res = await client.get(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200

    async def test_create_student(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/v1/students — create a new student."""
        token = await _get_admin_token(client, db_session)
        res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Test Student",
                "department": "CS",
                "enrollment_year": 2024,
            },
        )
        assert res.status_code in (200, 201)

    async def test_list_students_filter_by_course(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/v1/students?course_id=... returns only students observed in that course."""
        token = await _get_admin_token(client, db_session)

        student_a_res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Course Student A", "department": "CS", "enrollment_year": 2024},
        )
        assert student_a_res.status_code in (200, 201)
        student_a_id = int(student_a_res.json()["id"])

        student_b_res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Course Student B", "department": "CS", "enrollment_year": 2024},
        )
        assert student_b_res.status_code in (200, 201)
        student_b_id = int(student_b_res.json()["id"])

        instructor = User(
            email=f"inst-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestInstructor123!"),
            role="instructor",
            is_active=True,
        )
        db_session.add(instructor)
        await db_session.flush()

        course_res = await client.post(
            "/api/v1/courses",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "code": "CS101",
                "name": "Intro to Testing",
                "instructor_id": int(instructor.id),
                "department": "CS",
            },
        )
        assert course_res.status_code in (200, 201)
        course_id = int(course_res.json()["id"])

        room_res = await client.post(
            "/api/v1/rooms",
            headers={"Authorization": f"Bearer {token}"},
            json={"room_name": "Room A", "capacity": 30},
        )
        assert room_res.status_code in (200, 201)
        room_id = int(room_res.json()["id"])

        schedule_res = await client.post(
            "/api/v1/schedules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "course_id": course_id,
                "room_id": room_id,
                "start_time": "09:00:00",
                "end_time": "10:00:00",
                "days_of_week": ["Monday"],
            },
        )
        assert schedule_res.status_code in (200, 201)
        schedule_id = int(schedule_res.json()["id"])

        snapshot = Snapshot(schedule_id=schedule_id, expected_count=2)
        db_session.add(snapshot)
        await db_session.flush()

        db_session.add(
            Detection(
                snapshot_id=int(snapshot.id),
                student_id=student_a_id,
                confidence=0.91,
                camera_id="cam-test-1",
            )
        )
        await db_session.flush()

        filtered_res = await client.get(
            f"/api/v1/students?course_id={course_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert filtered_res.status_code == 200
        payload = filtered_res.json()
        assert len(payload) == 1
        assert int(payload[0]["id"]) == student_a_id
        assert int(payload[0]["course_count"]) >= 1
        assert course_id in payload[0]["course_ids"]
        assert all(int(item["id"]) != student_b_id for item in payload)

    async def test_guided_burst_enrollment_to_verification_pass(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """E2E API flow: guided-burst style enrollment completes and verification test passes."""
        token = await _get_admin_token(client, db_session)

        create_res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Guided Burst Student", "department": "AI", "enrollment_year": 2026},
        )
        assert create_res.status_code in (200, 201)
        student_id = int(create_res.json()["id"])

        from backend.api.v1 import students as students_api

        monkeypatch.setattr(
            students_api.ai_pipeline,
            "detect_faces_sahi",
            lambda _image: [(0, 0, 128, 128)],
        )
        monkeypatch.setattr(
            students_api.ai_pipeline,
            "face_quality_score",
            lambda **_kwargs: (0.96, 130.0),
        )

        embed_idx = {"value": 0}

        def _embedding(_crop):
            idx = embed_idx["value"]
            embed_idx["value"] += 1
            emb = np.zeros(512, dtype=np.float32)
            emb[idx % 512] = 1.0
            return emb

        pose_values = [
            ("frontal", 0.92),
            ("frontal", 0.93),
            ("left_34", 0.94),
            ("frontal", 0.90),
            ("right_34", 0.95),
            ("frontal", 0.91),
            ("left_34", 0.92),
            ("right_34", 0.93),
        ]
        pose_idx = {"value": 0}

        def _pose(_crop):
            idx = pose_idx["value"]
            pose_idx["value"] += 1
            return pose_values[idx % len(pose_values)]

        monkeypatch.setattr(students_api.ai_pipeline, "extract_embedding_lvface", _embedding)
        monkeypatch.setattr(students_api.ai_pipeline, "estimate_pose_label", _pose)

        blank = np.zeros((160, 160, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", blank)
        assert ok
        image_bytes = encoded.tobytes()

        burst_files = [
            ("images", (f"guided_{idx}.jpg", image_bytes, "image/jpeg"))
            for idx in range(8)
        ]

        enroll_res = await client.post(
            f"/api/v1/students/{student_id}/enroll/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"pose_label": "frontal", "auto_pose": "true"},
            files=burst_files,
        )
        assert enroll_res.status_code == 200
        enroll_payload = enroll_res.json()
        assert enroll_payload["enrolled"] is True
        assert int(enroll_payload["new_embeddings"]) >= 5
        assert enroll_payload["missing_pose_coverage"] == {}
        assert isinstance(enroll_payload.get("reject_reason_groups"), dict)
        assert isinstance(enroll_payload.get("capture_guidance"), list)

        def _probe_embedding(_crop):
            emb = np.zeros(512, dtype=np.float32)
            emb[0] = 1.0
            return emb

        monkeypatch.setattr(students_api.ai_pipeline, "extract_embedding_lvface", _probe_embedding)

        verify_res = await client.post(
            f"/api/v1/students/{student_id}/enrollment/test",
            headers={"Authorization": f"Bearer {token}"},
            files={"image": ("probe.jpg", image_bytes, "image/jpeg")},
        )
        assert verify_res.status_code == 200
        verify_payload = verify_res.json()
        assert verify_payload["is_match"] is True
        assert int(verify_payload["best_match_student_id"]) == student_id

    async def test_enroll_student_from_images(self, client: AsyncClient, db_session: AsyncSession, monkeypatch):
        """POST /api/v1/students/{id}/enroll/images — creates embeddings and enrolls."""
        token = await _get_admin_token(client, db_session)

        create_res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "Enroll Me",
                "department": "IT",
                "enrollment_year": 2025,
            },
        )
        assert create_res.status_code in (200, 201)
        student_id = create_res.json()["id"]

        from backend.api.v1 import students as students_api

        monkeypatch.setattr(
            students_api.ai_pipeline,
            "detect_faces_sahi",
            lambda _image: [(0, 0, 128, 128)],
        )
        monkeypatch.setattr(
            students_api.ai_pipeline,
            "face_quality_score",
            lambda **_kwargs: (0.95, 120.0),
        )

        call_index = {"value": 0}

        def _next_embedding(_crop):
            idx = call_index["value"]
            call_index["value"] += 1
            emb = np.zeros(512, dtype=np.float32)
            emb[idx % 512] = 1.0
            return emb

        monkeypatch.setattr(
            students_api.ai_pipeline,
            "extract_embedding_lvface",
            _next_embedding,
        )
        monkeypatch.setattr(
            students_api.ai_pipeline,
            "estimate_pose_label",
            lambda _crop: ("frontal", 0.2),
        )

        blank = np.zeros((160, 160, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", blank)
        assert ok
        image_bytes = encoded.tobytes()

        frontal_files = [
            ("images", (f"frontal_{idx}.jpg", image_bytes, "image/jpeg"))
            for idx in range(3)
        ]
        left_files = [("images", ("left_1.jpg", image_bytes, "image/jpeg"))]
        right_files = [("images", ("right_1.jpg", image_bytes, "image/jpeg"))]

        enroll_frontal = await client.post(
            f"/api/v1/students/{student_id}/enroll/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"pose_label": "frontal", "auto_pose": "false"},
            files=frontal_files,
        )
        assert enroll_frontal.status_code == 200
        assert enroll_frontal.json()["enrolled"] is False

        enroll_left = await client.post(
            f"/api/v1/students/{student_id}/enroll/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"pose_label": "left_34", "auto_pose": "false"},
            files=left_files,
        )
        assert enroll_left.status_code == 200
        assert enroll_left.json()["enrolled"] is False

        enroll_right = await client.post(
            f"/api/v1/students/{student_id}/enroll/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"pose_label": "right_34", "auto_pose": "false"},
            files=right_files,
        )
        assert enroll_right.status_code == 200
        payload = enroll_right.json()
        assert payload["student_id"] == student_id
        assert payload["new_embeddings"] == 1
        assert payload["enrolled"] is True
        assert payload["pose_coverage"]["frontal"] >= 3
        assert payload["pose_coverage"]["left_34"] >= 1
        assert payload["pose_coverage"]["right_34"] >= 1

        quality_res = await client.get(
            f"/api/v1/students/{student_id}/enrollment/quality",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert quality_res.status_code == 200
        quality = quality_res.json()
        assert quality["student_id"] == student_id
        assert quality["active_embeddings"] >= 5
        assert quality["total_embeddings"] >= 5
        assert quality["missing_pose_coverage"] == {}
        assert any(bucket["active_count"] > 0 for bucket in quality["buckets"])

        templates_res = await client.get(
            f"/api/v1/students/{student_id}/enrollment/templates",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert templates_res.status_code == 200
        templates = templates_res.json()
        assert len(templates) >= 5

        def _probe_embedding(_crop):
            emb = np.zeros(512, dtype=np.float32)
            emb[0] = 1.0
            return emb

        monkeypatch.setattr(
            students_api.ai_pipeline,
            "extract_embedding_lvface",
            _probe_embedding,
        )

        test_res = await client.post(
            f"/api/v1/students/{student_id}/enrollment/test",
            headers={"Authorization": f"Bearer {token}"},
            files={"image": ("test_probe.jpg", image_bytes, "image/jpeg")},
        )
        assert test_res.status_code == 200
        test_payload = test_res.json()
        assert test_payload["student_id"] == student_id
        assert test_payload["best_match_student_id"] == student_id
        assert test_payload["is_match"] is True
        assert len(test_payload["candidates"]) >= 1

        update_res = await client.patch(
            f"/api/v1/students/{student_id}/enrollment/templates/{templates[0]['id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"template_status": "backup"},
        )
        assert update_res.status_code == 200
        updated = update_res.json()
        assert updated["template_status"] == "backup"


@pytest.mark.asyncio
class TestAttendanceAPI:
    """Test attendance query endpoints."""

    async def test_attendance_no_auth(self, client: AsyncClient):
        """GET /api/v1/attendance/{schedule_id} — requires auth."""
        res = await client.get("/api/v1/attendance/1")
        assert res.status_code in (401, 403)


@pytest.mark.asyncio
class TestSystemAPI:
    """Test system endpoints."""

    async def test_system_health_db(self, client: AsyncClient, db_session: AsyncSession):
        """GET /api/v1/health — DB + Redis connectivity check."""
        token = await _get_admin_token(client, db_session)
        res = await client.get(
            "/api/v1/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200

    async def test_tracking_diagnostics_endpoint(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """GET /api/v1/tracks returns per-camera tracking diagnostics."""
        admin = User(
            email=f"tracks-admin-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestAdmin123!"),
            role="admin",
            is_active=True,
        )
        db_session.add(admin)
        await db_session.flush()
        token = create_access_token(subject=int(admin.id), role="admin")

        from backend.api.v1 import system as system_api

        async def _fake_redis_stats(_url: str):
            return [
                {
                    "camera_id": "cam-1",
                    "active_tracks": 4,
                    "confirmed_tracks": 2,
                    "average_track_age_seconds": 11.5,
                }
            ]

        async def _fake_cross_camera_stats(_url: str):
            return {
                "link_count": 7,
                "rejected_link_count": 2,
                "confidence_distribution": {
                    "0.0-0.4": 0,
                    "0.4-0.6": 1,
                    "0.6-0.8": 3,
                    "0.8-1.0": 3,
                },
            }

        monkeypatch.setattr(system_api, "_load_track_stats_from_redis", _fake_redis_stats)
        monkeypatch.setattr(
            system_api,
            "_load_cross_camera_stats_from_redis",
            _fake_cross_camera_stats,
        )

        res = await client.get(
            "/api/v1/tracks",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        payload = res.json()
        assert "tracking_enabled" in payload
        assert payload["camera_count"] == 1
        assert payload["active_tracks"] == 4
        assert payload["confirmed_tracks"] == 2
        assert len(payload["cameras"]) == 1
        assert payload["cameras"][0]["camera_id"] == "cam-1"
        assert int(payload["cross_camera"]["link_count"]) == 7
        assert int(payload["cross_camera"]["rejected_link_count"]) == 2

    async def test_multi_face_testing_endpoint(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """POST /api/v1/testing/multi-face returns classroom metrics and matches."""
        token = await _get_admin_token(client, db_session)

        students = [
            Student(name="A", department="QA", enrollment_year=2026, is_enrolled=True),
            Student(name="B", department="QA", enrollment_year=2026, is_enrolled=True),
            Student(name="C", department="QA", enrollment_year=2026, is_enrolled=True),
        ]
        db_session.add_all(students)
        await db_session.flush()

        from backend.services.ai_pipeline import ai_pipeline, FaceMatch

        monkeypatch.setattr(
            ai_pipeline,
            "detect_faces_sahi",
            lambda _image: [(0, 0, 80, 80), (90, 0, 80, 80), (180, 0, 80, 80)],
        )
        monkeypatch.setattr(
            ai_pipeline,
            "recognize",
            lambda db_session, image_bgr, schedule_id: [
                FaceMatch(student_id=int(students[0].id), confidence=0.94, bbox=(0, 0, 80, 80), quality=0.91),
                FaceMatch(student_id=int(students[2].id), confidence=0.90, bbox=(180, 0, 80, 80), quality=0.89),
            ],
        )

        blank = np.zeros((240, 320, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", blank)
        assert ok
        image_bytes = encoded.tobytes()

        res = await client.post(
            "/api/v1/testing/multi-face",
            headers={"Authorization": f"Bearer {token}"},
            files=[
                ("image", ("classroom.jpg", image_bytes, "image/jpeg")),
                ("expected_student_ids", (None, str(int(students[0].id)))),
                ("expected_student_ids", (None, str(int(students[1].id)))),
            ],
        )
        assert res.status_code == 200

        payload = res.json()
        assert payload["detected_faces"] == 3
        assert payload["recognized_faces"] == 2
        assert len(payload["detections"]) == 3
        assert payload["expected_faces"] == 2
        assert payload["true_positive"] == 1
        assert payload["false_positive"] == 1
        assert payload["false_negative"] == 1
        assert abs(float(payload["precision"]) - 0.5) < 1e-6
        assert abs(float(payload["recall"]) - 0.5) < 1e-6
        assert payload["annotated_image_b64"] is not None
        assert payload["annotated_detections_image_b64"] is not None

        names = {item["student_name"] for item in payload["matches"]}
        assert "A" in names
        assert "C" in names


@pytest.mark.asyncio
class TestAdminAndReportingAPI:
    """Additional endpoint coverage for admin, reporting, and SSE modules."""

    async def test_users_crud_and_rbac(self, client: AsyncClient, db_session: AsyncSession):
        """Admin can manage users; non-admin role is blocked from user list."""
        admin_token = await _get_admin_token(client, db_session)

        create_res = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "email": f"managed-{uuid4().hex[:8]}@test.io",
                "password": "ManagedPass123!",
                "role": "instructor",
                "is_active": True,
            },
        )
        assert create_res.status_code in (200, 201)
        created = create_res.json()
        managed_user_id = int(created["id"])

        list_res = await client.get(
            "/api/v1/users?role=instructor",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert list_res.status_code == 200
        users_payload = list_res.json()
        assert any(int(item["id"]) == managed_user_id for item in users_payload)

        get_res = await client.get(
            f"/api/v1/users/{managed_user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert get_res.status_code == 200
        assert int(get_res.json()["id"]) == managed_user_id

        patch_res = await client.patch(
            f"/api/v1/users/{managed_user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": False},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["is_active"] is False

        instructor = User(
            email=f"forbidden-inst-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestInstructor123!"),
            role="instructor",
            is_active=True,
        )
        db_session.add(instructor)
        await db_session.flush()
        instructor_token = create_access_token(subject=int(instructor.id), role="instructor")

        forbidden_res = await client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {instructor_token}"},
        )
        assert forbidden_res.status_code == 403

        delete_res = await client.delete(
            f"/api/v1/users/{managed_user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert delete_res.status_code == 204

    async def test_courses_crud(self, client: AsyncClient, db_session: AsyncSession):
        """Admin can create, list, fetch, and update courses."""
        token = await _get_admin_token(client, db_session)

        instructor = User(
            email=f"course-inst-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestInstructor123!"),
            role="instructor",
            is_active=True,
        )
        db_session.add(instructor)
        await db_session.flush()

        create_res = await client.post(
            "/api/v1/courses",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "code": "QA201",
                "name": "API Verification",
                "instructor_id": int(instructor.id),
                "department": "QA",
            },
        )
        assert create_res.status_code in (200, 201)
        course_id = int(create_res.json()["id"])

        list_res = await client.get(
            "/api/v1/courses",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert list_res.status_code == 200
        assert any(int(item["id"]) == course_id for item in list_res.json())

        get_res = await client.get(
            f"/api/v1/courses/{course_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_res.status_code == 200
        assert int(get_res.json()["id"]) == course_id

        patch_res = await client.patch(
            f"/api/v1/courses/{course_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "API Verification Updated"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["name"] == "API Verification Updated"

    async def test_attendance_report_and_csv_export(self, client: AsyncClient, db_session: AsyncSession):
        """Attendance report and CSV export return expected payload shape."""
        token = await _get_admin_token(client, db_session)

        student_res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Report Student", "department": "CS", "enrollment_year": 2026},
        )
        assert student_res.status_code in (200, 201)
        student_id = int(student_res.json()["id"])

        instructor = User(
            email=f"report-inst-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestInstructor123!"),
            role="instructor",
            is_active=True,
        )
        db_session.add(instructor)
        await db_session.flush()

        course_res = await client.post(
            "/api/v1/courses",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "code": "CS250",
                "name": "Attendance Analytics",
                "instructor_id": int(instructor.id),
                "department": "CS",
            },
        )
        assert course_res.status_code in (200, 201)
        course_id = int(course_res.json()["id"])

        room_res = await client.post(
            "/api/v1/rooms",
            headers={"Authorization": f"Bearer {token}"},
            json={"room_name": "Room Report", "capacity": 40},
        )
        assert room_res.status_code in (200, 201)
        room_id = int(room_res.json()["id"])

        schedule_res = await client.post(
            "/api/v1/schedules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "course_id": course_id,
                "room_id": room_id,
                "start_time": "10:00:00",
                "end_time": "11:00:00",
                "days_of_week": ["Tuesday"],
            },
        )
        assert schedule_res.status_code in (200, 201)
        schedule_id = int(schedule_res.json()["id"])

        snapshot_1 = Snapshot(schedule_id=schedule_id, expected_count=1)
        snapshot_2 = Snapshot(schedule_id=schedule_id, expected_count=1)
        db_session.add_all([snapshot_1, snapshot_2])
        await db_session.flush()
        db_session.add(
            Detection(
                snapshot_id=int(snapshot_1.id),
                student_id=student_id,
                confidence=0.96,
                camera_id="cam-report-1",
            )
        )
        await db_session.flush()

        report_res = await client.get(
            f"/api/v1/attendance/{schedule_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert report_res.status_code == 200
        report = report_res.json()
        assert int(report["schedule_id"]) == schedule_id
        assert int(report["total_snapshots"]) == 2
        assert len(report["records"]) >= 1
        assert int(report["records"][0]["student_id"]) == student_id

        export_res = await client.get(
            f"/api/v1/attendance/{schedule_id}/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert export_res.status_code == 200
        assert "text/csv" in export_res.headers.get("content-type", "")
        assert "student_id" in export_res.text

    async def test_sse_status_and_auth_failure_paths(
        self,
        client: AsyncClient,
    ):
        """SSE status is reachable and attendance stream rejects invalid token."""
        status_res = await client.get("/api/v1/sse/status")
        assert status_res.status_code == 200
        status_payload = status_res.json()
        assert "total_subscribers" in status_payload
        assert "system_subscribers" in status_payload

        invalid_res = await client.get("/api/v1/sse/attendance/1?token=invalid-token")
        assert invalid_res.status_code == 401


@pytest.mark.asyncio
class TestIngestAPI:
    """Integration coverage for HMAC-authenticated ingest dispatch routes."""

    async def test_ingest_snapshot_dispatches_task(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """POST /api/v1/ingest dispatches snapshot task with valid signed request."""
        from backend.api.v1 import ingest as ingest_api

        async def _allow_rate_limit(_key: str, max_requests: int, window_seconds: int):
            return True

        async def _store_nonce(_device_id: str, _nonce: str, ttl_seconds: int):
            return True

        class _TaskResult:
            id = "task-snapshot-123"

        dispatched: dict[str, object] = {}

        def _send_task(task_name: str, kwargs: dict):
            dispatched["task_name"] = task_name
            dispatched["kwargs"] = kwargs
            return _TaskResult()

        monkeypatch.setattr(ingest_api, "check_rate_limit", _allow_rate_limit)
        monkeypatch.setattr(ingest_api, "store_nonce", _store_nonce)
        monkeypatch.setattr(ingest_api.celery_app, "send_task", _send_task)

        room = Room(room_name=f"Ingest Room {uuid4().hex[:6]}", capacity=50)
        db_session.add(room)
        await db_session.flush()

        instructor = User(
            email=f"ingest-inst-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestInstructor123!"),
            role="instructor",
            is_active=True,
        )
        db_session.add(instructor)
        await db_session.flush()

        course = Course(
            code=f"IG{uuid4().hex[:4]}",
            name="Ingest Validation",
            instructor_id=int(instructor.id),
            department="QA",
        )
        db_session.add(course)
        await db_session.flush()

        schedule = Schedule(
            course_id=int(course.id),
            room_id=int(room.id),
            start_time=time(hour=9, minute=0),
            end_time=time(hour=10, minute=0),
            days_of_week=["Monday"],
        )
        db_session.add(schedule)
        await db_session.flush()

        device_secret = "device-secret-snapshot"
        device = Device(
            room_id=int(room.id),
            secret_key_hash=hash_device_secret(device_secret),
            type="camera",
        )
        db_session.add(device)
        await db_session.flush()

        blank = np.zeros((64, 64, 3), dtype=np.uint8)
        ok, encoded = cv2.imencode(".jpg", blank)
        assert ok
        image_bytes = encoded.tobytes()

        timestamp = str(int(pytime.time()))
        nonce = f"nonce-{uuid4().hex}"
        payload_digest = compute_payload_digest(image_bytes, int(device.id), timestamp)
        signature = sign_payload(payload_digest, nonce, device_secret)

        res = await client.post(
            "/api/v1/ingest",
            data={
                "device_id": str(int(device.id)),
                "schedule_id": str(int(schedule.id)),
                "camera_id": "cam-ingest-1",
                "timestamp": timestamp,
                "nonce": nonce,
            },
            files={"image": ("snapshot.jpg", image_bytes, "image/jpeg")},
            headers={
                "X-Signature": signature,
                "X-Device-Secret": device_secret,
            },
        )

        assert res.status_code == 200
        payload = res.json()
        assert payload["status"] == "dispatched"
        assert payload["task_id"] == "task-snapshot-123"
        assert dispatched.get("task_name") == "backend.workers.cv_tasks.process_snapshot"
        assert isinstance(dispatched.get("kwargs"), dict)

    async def test_ingest_clip_dispatches_task(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """POST /api/v1/ingest/clip dispatches clip task with valid signed request."""
        from backend.api.v1 import ingest as ingest_api

        async def _allow_rate_limit(_key: str, max_requests: int, window_seconds: int):
            return True

        async def _store_nonce(_device_id: str, _nonce: str, ttl_seconds: int):
            return True

        class _TaskResult:
            id = "task-clip-123"

        dispatched: dict[str, object] = {}

        def _send_task(task_name: str, kwargs: dict):
            dispatched["task_name"] = task_name
            dispatched["kwargs"] = kwargs
            return _TaskResult()

        monkeypatch.setattr(ingest_api, "check_rate_limit", _allow_rate_limit)
        monkeypatch.setattr(ingest_api, "store_nonce", _store_nonce)
        monkeypatch.setattr(ingest_api.celery_app, "send_task", _send_task)

        room = Room(room_name=f"Clip Room {uuid4().hex[:6]}", capacity=40)
        db_session.add(room)
        await db_session.flush()

        instructor = User(
            email=f"ingest-clip-inst-{uuid4().hex[:8]}@test.io",
            password_hash=hash_password("TestInstructor123!"),
            role="instructor",
            is_active=True,
        )
        db_session.add(instructor)
        await db_session.flush()

        course = Course(
            code=f"CL{uuid4().hex[:4]}",
            name="Ingest Clip Validation",
            instructor_id=int(instructor.id),
            department="QA",
        )
        db_session.add(course)
        await db_session.flush()

        schedule = Schedule(
            course_id=int(course.id),
            room_id=int(room.id),
            start_time=time(hour=11, minute=0),
            end_time=time(hour=12, minute=0),
            days_of_week=["Tuesday"],
        )
        db_session.add(schedule)
        await db_session.flush()

        device_secret = "device-secret-clip"
        device = Device(
            room_id=int(room.id),
            secret_key_hash=hash_device_secret(device_secret),
            type="camera",
        )
        db_session.add(device)
        await db_session.flush()

        clip_bytes = b"fake-clip-bytes-for-dispatch"
        timestamp = str(int(pytime.time()))
        nonce = f"nonce-{uuid4().hex}"
        payload_digest = compute_payload_digest(clip_bytes, int(device.id), timestamp)
        signature = sign_payload(payload_digest, nonce, device_secret)

        res = await client.post(
            "/api/v1/ingest/clip",
            data={
                "device_id": str(int(device.id)),
                "schedule_id": str(int(schedule.id)),
                "camera_id": "cam-ingest-clip-1",
                "timestamp": timestamp,
                "nonce": nonce,
            },
            files={"clip": ("clip.mp4", clip_bytes, "video/mp4")},
            headers={
                "X-Signature": signature,
                "X-Device-Secret": device_secret,
            },
        )

        assert res.status_code == 200
        payload = res.json()
        assert payload["status"] == "dispatched"
        assert payload["task_id"] == "task-clip-123"
        assert dispatched.get("task_name") == "backend.workers.cv_tasks.process_clip"
        assert isinstance(dispatched.get("kwargs"), dict)


@pytest.mark.asyncio
class TestRealtimeAPI:
    """Runtime-path checks for SSE and WebSocket attendance endpoints."""

    async def test_attendance_sse_stream_emits_detection_event(
        self,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """Attendance SSE stream yields published events for authorized users."""
        from backend.api.v1 import sse as sse_api

        async def _authorized_user(_token: str, _db: AsyncSession):
            return SimpleNamespace(role="admin")

        monkeypatch.setattr(sse_api, "get_current_user_from_token", _authorized_user)

        schedule_id = 2201
        before_count = sse_api.sse_broadcaster.total_subscribers
        response = await sse_api.attendance_sse_stream(
            schedule_id=schedule_id,
            request=_FakeSSERequest(),
            token="token-ok",
            db=db_session,
        )

        sent = await sse_api.sse_broadcaster.publish(
            schedule_id,
            {
                "type": "detection",
                "student_id": 42,
                "confidence": 0.97,
            },
        )
        assert sent == 1

        chunk = await anext(response.body_iterator)
        chunk_text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk
        assert "event: detection" in chunk_text
        assert '"student_id": 42' in chunk_text

        await response.body_iterator.aclose()
        assert sse_api.sse_broadcaster.total_subscribers == before_count

    async def test_attendance_ws_rejects_invalid_token(
        self,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """Attendance websocket closes with unauthorized reason on invalid token."""
        from backend.api.v1 import websocket as ws_api

        async def _invalid_token(_token: str, _db: AsyncSession):
            raise HTTPException(status_code=401, detail="invalid token")

        monkeypatch.setattr(ws_api, "get_current_user_from_token", _invalid_token)

        fake_socket = _FakeAttendanceSocket()
        await ws_api.attendance_ws(
            websocket=fake_socket,
            schedule_id=3201,
            token="bad-token",
            db=db_session,
        )

        assert fake_socket.accepted is False
        assert fake_socket.closed == [(1008, "Unauthorized")]

    async def test_attendance_ws_accepts_admin_and_unsubscribes_on_disconnect(
        self,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """Attendance websocket accepts admin token and cleans up on disconnect."""
        from backend.api.v1 import websocket as ws_api

        async def _authorized_user(_token: str, _db: AsyncSession):
            return SimpleNamespace(role="admin")

        monkeypatch.setattr(ws_api, "get_current_user_from_token", _authorized_user)

        schedule_id = 3202
        before_count = ws_api.attendance_broadcaster.subscriber_count
        fake_socket = _FakeAttendanceSocket()

        await ws_api.attendance_ws(
            websocket=fake_socket,
            schedule_id=schedule_id,
            token="good-token",
            db=db_session,
        )

        assert fake_socket.accepted is True
        assert fake_socket.closed == []
        assert ws_api.attendance_broadcaster.subscriber_count == before_count
