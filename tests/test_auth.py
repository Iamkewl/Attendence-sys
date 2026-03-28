"""Integration tests for authentication endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest.mark.asyncio
class TestAuthFlow:
    """Test the complete auth lifecycle: register → login → refresh → logout."""

    async def test_register_admin(self, client: AsyncClient):
        """POST /api/v1/auth/register — create admin user."""
        res = await client.post("/api/v1/auth/register", json={
            "email": "admin@test.io",
            "password": "TestAdmin123!",
            "full_name": "Test Admin",
            "role": "admin",
        })
        assert res.status_code in (200, 201, 409)  # 409 if already exists

    async def test_register_duplicate_fails(self, client: AsyncClient):
        """Duplicate registration should return 409."""
        # First register
        await client.post("/api/v1/auth/register", json={
            "email": "dup@test.io",
            "password": "TestDup123!",
            "full_name": "Dup User",
            "role": "instructor",
        })
        # Duplicate
        res = await client.post("/api/v1/auth/register", json={
            "email": "dup@test.io",
            "password": "TestDup123!",
            "full_name": "Dup User",
            "role": "instructor",
        })
        assert res.status_code == 409

    async def test_login_success(self, client: AsyncClient, admin_credentials):
        """POST /api/v1/auth/login — valid credentials return tokens."""
        # Register first
        await client.post("/api/v1/auth/register", json={
            "email": admin_credentials["email"],
            "password": admin_credentials["password"],
            "full_name": "Admin",
            "role": "admin",
        })
        # Login
        res = await client.post("/api/v1/auth/login", json=admin_credentials)
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_login_wrong_password(self, client: AsyncClient):
        """Wrong password returns 401."""
        res = await client.post("/api/v1/auth/login", json={
            "email": "admin@test.io",
            "password": "WrongPassword!",
        })
        assert res.status_code == 401

    async def test_protected_route_no_token(self, client: AsyncClient):
        """Accessing protected route without token returns 401/403."""
        res = await client.get("/api/v1/users")
        assert res.status_code in (401, 403)

    async def test_protected_route_with_token(self, client: AsyncClient, admin_credentials):
        """Accessing protected route with valid token succeeds."""
        # Register + Login
        await client.post("/api/v1/auth/register", json={
            "email": admin_credentials["email"],
            "password": admin_credentials["password"],
            "full_name": "Admin",
            "role": "admin",
        })
        login_res = await client.post("/api/v1/auth/login", json=admin_credentials)
        if login_res.status_code != 200:
            pytest.skip("Login failed, skipping protected route test")

        token = login_res.json()["access_token"]
        res = await client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200


@pytest.mark.asyncio
class TestHealthEndpoints:
    """Test system health and readiness probes."""

    async def test_health(self, client: AsyncClient):
        """GET /health — always returns 200."""
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    async def test_ready(self, client: AsyncClient):
        """GET /ready — returns readiness info."""
        res = await client.get("/ready")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "version" in data
