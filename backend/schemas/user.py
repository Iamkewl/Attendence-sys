"""User request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Admin-level user creation."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="student", pattern="^(admin|instructor|student|device)$")
    is_active: bool = True


class UserRead(BaseModel):
    """User response DTO."""

    id: int
    email: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    """User update (admin only)."""

    email: EmailStr | None = None
    role: str | None = Field(default=None, pattern="^(admin|instructor|student|device)$")
    is_active: bool | None = None
