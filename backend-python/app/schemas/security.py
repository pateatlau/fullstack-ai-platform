from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.ai.security.redaction import (
    REDACTED_PLACEHOLDER,
    is_sensitive_key,
    redact_secret_patterns,
)


def _redact_audit_metadata(value: Any, *, key: str | None = None) -> Any:
    if key is not None and is_sensitive_key(key):
        return REDACTED_PLACEHOLDER
    if isinstance(value, dict):
        return {
            str(child_key): _redact_audit_metadata(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_audit_metadata(item) for item in value]
    if isinstance(value, str):
        return redact_secret_patterns(value)
    return value


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


class SecurityUserSummaryResponse(BaseModel):
    id: uuid.UUID
    email: str | None = None
    display_name: str | None = None
    roles: list[SecurityUserRoleResponse] = Field(default_factory=list)


class SecurityUserListResponse(BaseModel):
    items: list[SecurityUserSummaryResponse] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


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

    @field_validator("metadata", mode="before")
    @classmethod
    def redact_metadata(cls, value: Any) -> dict[str, Any]:
        redacted = _redact_audit_metadata(value)
        return redacted if isinstance(redacted, dict) else {}


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
    rate_limits_per_minute: dict[str, int] = Field(default_factory=dict)


class SecurityRoleAssignmentRequest(BaseModel):
    role_name: str
