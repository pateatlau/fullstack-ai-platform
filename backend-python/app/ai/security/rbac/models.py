from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.ai.security.errors import SecurityErrorCode


class Role(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    is_system: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Permission(BaseModel):
    id: uuid.UUID
    key: str
    description: str


class UserRoleAssignment(BaseModel):
    user_id: uuid.UUID
    role_name: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    permission_key: str
    matched_role: str | None = None
    matched_permission: str | None = None
    denial_reason: SecurityErrorCode | None = None


class PermissionDefinition(BaseModel):
    key: str
    display_name: str
    description: str
    category: Literal[
        "rbac",
        "audit",
        "policy",
        "jobs",
        "approvals",
        "tools",
        "plugins",
        "workflow",
        "mcp",
    ]
    risk_level: Literal["low", "medium", "high"]
    reserved: bool = False
