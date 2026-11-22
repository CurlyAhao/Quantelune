"""Pydantic schemas for authentication."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Registration payload."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str | None = Field(None, max_length=255)


class LoginRequest(BaseModel):
    """Login payload."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Public user representation."""

    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    """Returned on register/login — user info only (tokens are in cookies)."""

    user: UserResponse
    message: str = "ok"
