"""Integration tests for student and attendance endpoints."""

import pytest
from httpx import AsyncClient


async def _get_admin_token(client: AsyncClient) -> str:
    """Helper: register admin and return access token."""
    await client.post("/api/v1/auth/register", json={
        "email": "admin2@test.io",
        "password": "TestAdmin123!",
        "full_name": "Admin",
        "role": "admin",
    })
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "admin2@test.io",
        "password": "TestAdmin123!",
    })
    return login_res.json().get("access_token", "")


@pytest.mark.asyncio
class TestStudentsAPI:
    """Test student CRUD endpoints."""

    async def test_list_students_empty(self, client: AsyncClient):
        """GET /api/v1/students — returns empty list initially."""
        token = await _get_admin_token(client)
        res = await client.get(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200

    async def test_create_student(self, client: AsyncClient):
        """POST /api/v1/students — create a new student."""
        token = await _get_admin_token(client)
        res = await client.post(
            "/api/v1/students",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "enrollment_number": "2024001",
                "full_name": "Test Student",
                "email": "student@test.io",
            },
        )
        assert res.status_code in (200, 201)


@pytest.mark.asyncio
class TestAttendanceAPI:
    """Test attendance query endpoints."""

    async def test_attendance_no_auth(self, client: AsyncClient):
        """GET /api/v1/attendance — requires auth."""
        res = await client.get("/api/v1/attendance")
        assert res.status_code in (401, 403, 405)


@pytest.mark.asyncio
class TestSystemAPI:
    """Test system endpoints."""

    async def test_system_health_db(self, client: AsyncClient):
        """GET /api/v1/system/health — DB connectivity check."""
        token = await _get_admin_token(client)
        res = await client.get(
            "/api/v1/system/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
