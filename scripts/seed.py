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
"""

import asyncio
import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def seed():
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from backend.core.config import get_settings
    from backend.db.base import Base
    from backend.models.user import User
    from backend.models.room import Room, Device
    from backend.models.course import Course, CourseSection, Schedule
    from backend.models.student import Student
    from backend.core.security import hash_password

    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # ── Users ──────────────────────────────────
        admin = User(
            email="admin@attendai.io",
            full_name="System Admin",
            hashed_password=hash_password("Admin123!"),
            role="admin",
            is_active=True,
        )
        instructors = [
            User(email="ahmad@university.edu", full_name="Dr. Ahmad Rashid", hashed_password=hash_password("Instructor123!"), role="instructor", is_active=True),
            User(email="fatima@university.edu", full_name="Dr. Fatima Hassan", hashed_password=hash_password("Instructor123!"), role="instructor", is_active=True),
            User(email="sarah@university.edu", full_name="Prof. Sarah Ali", hashed_password=hash_password("Instructor123!"), role="instructor", is_active=True),
        ]
        device_user = User(
            email="device@attendai.local",
            full_name="IoT Device Service",
            hashed_password=hash_password("Device123!"),
            role="device",
            is_active=True,
        )
        db.add_all([admin, *instructors, device_user])

        # ── Rooms ──────────────────────────────────
        rooms = [
            Room(name="Lab A-204", building="Block A", floor=2, capacity=40),
            Room(name="Hall B-101", building="Block B", floor=1, capacity=100),
            Room(name="Room C-302", building="Block C", floor=3, capacity=30),
            Room(name="Lab D-105", building="Block D", floor=1, capacity=35),
        ]
        db.add_all(rooms)
        await db.flush()

        # ── Devices ────────────────────────────────
        devices = [
            Device(room_id=rooms[0].id, name="Cam-A1", device_type="camera", is_active=True),
            Device(room_id=rooms[0].id, name="Cam-A2", device_type="camera", is_active=True),
            Device(room_id=rooms[1].id, name="Cam-B1", device_type="camera", is_active=True),
            Device(room_id=rooms[2].id, name="Cam-C1", device_type="camera", is_active=True),
        ]
        db.add_all(devices)

        # ── Courses ────────────────────────────────
        courses = [
            Course(code="CS-301", name="Computer Vision", credits=3),
            Course(code="MATH-201", name="Linear Algebra", credits=3),
            Course(code="ENG-102", name="Technical Writing", credits=2),
        ]
        db.add_all(courses)
        await db.flush()

        # ── Sections ───────────────────────────────
        sections = [
            CourseSection(course_id=courses[0].id, section_code="A", instructor_id=instructors[0].id, semester="Spring 2026"),
            CourseSection(course_id=courses[1].id, section_code="A", instructor_id=instructors[1].id, semester="Spring 2026"),
            CourseSection(course_id=courses[2].id, section_code="A", instructor_id=instructors[2].id, semester="Spring 2026"),
        ]
        db.add_all(sections)
        await db.flush()

        # ── Schedules ──────────────────────────────
        schedules = [
            Schedule(section_id=sections[0].id, room_id=rooms[0].id, day_of_week=0, start_time="09:00", end_time="10:30"),
            Schedule(section_id=sections[0].id, room_id=rooms[0].id, day_of_week=2, start_time="09:00", end_time="10:30"),
            Schedule(section_id=sections[1].id, room_id=rooms[1].id, day_of_week=1, start_time="11:00", end_time="12:30"),
            Schedule(section_id=sections[1].id, room_id=rooms[1].id, day_of_week=3, start_time="11:00", end_time="12:30"),
            Schedule(section_id=sections[2].id, room_id=rooms[2].id, day_of_week=2, start_time="14:00", end_time="15:30"),
            Schedule(section_id=sections[2].id, room_id=rooms[2].id, day_of_week=4, start_time="14:00", end_time="15:30"),
        ]
        db.add_all(schedules)

        # ── Students ───────────────────────────────
        student_names = [
            "Ahmed Hassan", "Fatima Ali", "Omar Khalil", "Sara Ibrahim",
            "Yusuf Noor", "Layla Mahmoud", "Khaled Osman", "Nadia Saleh",
            "Tariq Yousef", "Amina Abdi", "Hassan Jama", "Maryam Farah",
            "Ibrahim Warsame", "Hodan Isse", "Abdi Mohamed", "Zamzam Ahmed",
            "Faisal Omar", "Rahma Abdullahi", "Mustafa Elmi", "Hawa Nur",
        ]
        students = []
        for i, name in enumerate(student_names):
            s = Student(
                enrollment_number=f"2024{i + 1:04d}",
                full_name=name,
                email=f"student{i + 1}@university.edu",
                is_enrolled=True,
            )
            students.append(s)
        db.add_all(students)

        await db.commit()
        print(f"✅ Seeded: {len([admin, *instructors, device_user])} users, "
              f"{len(rooms)} rooms, {len(devices)} devices, "
              f"{len(courses)} courses, {len(schedules)} schedules, "
              f"{len(students)} students")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
