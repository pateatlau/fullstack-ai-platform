from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SecurityRoleResponse(BaseModel):
    name: str
    description: str
    is_system: bool = True
    permissions: list[str] = Field(default_factory=list)


class SecurityUserRoleResponse(BaseModel):
    user_id: uuid.UUID
    role_name: str
    implicit: bool = False
    created_at: datetime | None = None


class SecurityAuditEntryResponse(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None = None
    actor_kind: str
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    trace_id: str | None = None
    source_ip_hash: str | None = None
    created_at: datetime | None = None


class SecurityAuditListResponse(BaseModel):
    items: list[SecurityAuditEntryResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class SecurityPolicySummaryResponse(BaseModel):
    security_governance_enabled: bool
    rbac_enforcement_enabled: bool
    guardrails_enabled: bool
    role_count: int
    permission_count: int
    guardrail_rule_count: int
    audit_retention_days: int
    security_guardrails_mode: str
    feature_flags: dict[str, bool] = Field(default_factory=dict)


class SecurityRoleAssignmentRequest(BaseModel):
    role_name: str
