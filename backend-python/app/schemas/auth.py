"""Request/response schemas for authentication endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class GoogleLoginRequest(BaseModel):
    id_token: str = Field(min_length=1, description="Google-issued ID token.")


class AuthenticatedUser(BaseModel):
    id: uuid.UUID
    email: str | None = None
    display_name: str | None = None
    picture_url: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthenticatedUser
