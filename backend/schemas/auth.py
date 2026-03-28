"""Authentication request/response schemas."""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """User registration request."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="student", pattern="^(admin|instructor|student)$")


class LoginRequest(BaseModel):
    """User login request."""

    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """JWT token pair response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class PasswordResetRequest(BaseModel):
    """Password reset initiation."""

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation with new password."""

    token: str
    new_password: str = Field(min_length=8, max_length=128)
