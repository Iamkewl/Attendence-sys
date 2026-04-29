"""Pytest configuration and shared fixtures.

Provides:
- Async test database (in-memory SQLite or test PostgreSQL)
- Async client for FastAPI integration tests
- Factory fixtures for users, students, etc.
"""

import asyncio
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Override database URL for tests
os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "sqlite+aiosqlite:///./test.db"
)
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-testing-only"
os.environ["TESTING"] = "true"

from backend.api.deps import get_db as deps_get_db
from backend.db.base import Base
from backend.main import app
from backend.db.session import get_async_session, get_db as session_get_db


# ── Engine / Session Fixtures ──────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    """Create test database engine."""
    url = os.environ["DATABASE_URL"]
    eng = create_async_engine(url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a transactional test session that rolls back after each test."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client with database session override."""

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[deps_get_db] = _override_session
    app.dependency_overrides[session_get_db] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Factory Helpers ────────────────────────────────────

@pytest.fixture
def admin_credentials():
    """Default admin user credentials."""
    return {"email": "admin@test.io", "password": "TestAdmin123!"}


@pytest.fixture
def instructor_credentials():
    """Default instructor credentials."""
    return {"email": "instructor@test.io", "password": "TestInstructor123!"}
