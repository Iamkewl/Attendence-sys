"""Database seed script — populates development data.

Usage:
    python -m scripts.seed

Creates:
- 1 admin user
- 3 instructor users
- 1 device user
- 4 rooms with 4 devices
- 3 courses with 6 schedules
- 20 students (enrolled)

Notes:
- Resets schema on each run (development-only).
"""

import asyncio
import os
import sys
from datetime import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def seed():
    from backend.core.config import get_settings
    from backend.core.security import hash_password
    from backend.db.base import Base
    from backend.models.course import Course, Schedule
    from backend.models.room import Device, Room
    from backend.models.student import Student
    from backend.models.user import User
    from backend.services.hmac_auth import hash_device_secret

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.begin() as conn:
        # pgvector extension is required for StudentEmbedding.vector columns.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Development reseed: reset all tables for deterministic test data.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # ── Users ──────────────────────────────────
        admin = User(
            email="admin@attendai.io",
            password_hash=hash_password("Admin123!"),
            role="admin",
            is_active=True,
        )
        instructors = [
            User(
                email="ahmad@university.edu",
                password_hash=hash_password("Instructor123!"),
                role="instructor",
                is_active=True,
            ),
            User(
                email="fatima@university.edu",
                password_hash=hash_password("Instructor123!"),
                role="instructor",
                is_active=True,
            ),
            User(
                email="sarah@university.edu",
                password_hash=hash_password("Instructor123!"),
                role="instructor",
                is_active=True,
            ),
        ]
        device_user = User(
            email="device@attendai.local",
            password_hash=hash_password("Device123!"),
            role="device",
            is_active=True,
        )
        db.add_all([admin, *instructors, device_user])
        await db.flush()

        # ── Rooms ──────────────────────────────────
        rooms = [
            Room(room_name="Lab A-204", capacity=40),
            Room(room_name="Hall B-101", capacity=100),
            Room(room_name="Room C-302", capacity=30),
            Room(room_name="Lab D-105", capacity=35),
        ]
        db.add_all(rooms)
        await db.flush()

        # ── Devices ────────────────────────────────
        devices = [
            Device(
                room_id=rooms[0].id,
                secret_key_hash=hash_device_secret("CamA1Secret!"),
                type="camera",
            ),
            Device(
                room_id=rooms[0].id,
                secret_key_hash=hash_device_secret("CamA2Secret!"),
                type="camera",
            ),
            Device(
                room_id=rooms[1].id,
                secret_key_hash=hash_device_secret("CamB1Secret!"),
                type="camera",
            ),
            Device(
                room_id=rooms[2].id,
                secret_key_hash=hash_device_secret("CamC1Secret!"),
                type="camera",
            ),
        ]
        db.add_all(devices)

        # ── Courses ────────────────────────────────
        courses = [
            Course(
                code="CS-301",
                name="Computer Vision",
                instructor_id=instructors[0].id,
                department="Computer Science",
            ),
            Course(
                code="MATH-201",
                name="Linear Algebra",
                instructor_id=instructors[1].id,
                department="Mathematics",
            ),
            Course(
                code="ENG-102",
                name="Technical Writing",
                instructor_id=instructors[2].id,
                department="English",
            ),
        ]
        db.add_all(courses)
        await db.flush()

        # ── Schedules ──────────────────────────────
        schedules = [
            Schedule(
                course_id=courses[0].id,
                room_id=rooms[0].id,
                start_time=time(9, 0),
                end_time=time(10, 30),
                days_of_week=["MON", "WED"],
            ),
            Schedule(
                course_id=courses[0].id,
                room_id=rooms[0].id,
                start_time=time(13, 0),
                end_time=time(14, 30),
                days_of_week=["FRI"],
            ),
            Schedule(
                course_id=courses[1].id,
                room_id=rooms[1].id,
                start_time=time(11, 0),
                end_time=time(12, 30),
                days_of_week=["TUE", "THU"],
            ),
            Schedule(
                course_id=courses[1].id,
                room_id=rooms[1].id,
                start_time=time(15, 0),
                end_time=time(16, 30),
                days_of_week=["WED"],
            ),
            Schedule(
                course_id=courses[2].id,
                room_id=rooms[2].id,
                start_time=time(14, 0),
                end_time=time(15, 30),
                days_of_week=["MON", "FRI"],
            ),
            Schedule(
                course_id=courses[2].id,
                room_id=rooms[2].id,
                start_time=time(8, 30),
                end_time=time(10, 0),
                days_of_week=["THU"],
            ),
        ]
        db.add_all(schedules)

        # ── Students ───────────────────────────────
        student_names = [
            "Ahmed Hassan",
            "Fatima Ali",
            "Omar Khalil",
            "Sara Ibrahim",
            "Yusuf Noor",
            "Layla Mahmoud",
            "Khaled Osman",
            "Nadia Saleh",
            "Tariq Yousef",
            "Amina Abdi",
            "Hassan Jama",
            "Maryam Farah",
            "Ibrahim Warsame",
            "Hodan Isse",
            "Abdi Mohamed",
            "Zamzam Ahmed",
            "Faisal Omar",
            "Rahma Abdullahi",
            "Mustafa Elmi",
            "Hawa Nur",
        ]
        students = []
        for name in student_names:
            students.append(
                Student(
                    name=name,
                    department="General Studies",
                    enrollment_year=2024,
                    is_enrolled=True,
                )
            )
        db.add_all(students)

        await db.commit()
        print(
            f"Seeded: {len([admin, *instructors, device_user])} users, "
            f"{len(rooms)} rooms, {len(devices)} devices, "
            f"{len(courses)} courses, {len(schedules)} schedules, "
            f"{len(students)} students"
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
