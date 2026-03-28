"""V1 API router — aggregates all domain routers under /api/v1/ prefix."""

from fastapi import APIRouter

from backend.api.v1.auth import router as auth_router
from backend.api.v1.users import router as users_router
from backend.api.v1.students import router as students_router
from backend.api.v1.courses import router as courses_router
from backend.api.v1.schedules import router as schedules_router
from backend.api.v1.rooms import router as rooms_router
from backend.api.v1.devices import router as devices_router
from backend.api.v1.ingest import router as ingest_router
from backend.api.v1.attendance import router as attendance_router
from backend.api.v1.system import router as system_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
v1_router.include_router(users_router, prefix="/users", tags=["Users"])
v1_router.include_router(students_router, prefix="/students", tags=["Students"])
v1_router.include_router(courses_router, prefix="/courses", tags=["Courses"])
v1_router.include_router(schedules_router, prefix="/schedules", tags=["Schedules"])
v1_router.include_router(rooms_router, prefix="/rooms", tags=["Rooms"])
v1_router.include_router(devices_router, prefix="/devices", tags=["Devices"])
v1_router.include_router(ingest_router, prefix="/ingest", tags=["Ingest"])
v1_router.include_router(attendance_router, prefix="/attendance", tags=["Attendance"])
v1_router.include_router(system_router, tags=["System"])
