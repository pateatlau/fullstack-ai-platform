"""Audit event domain model (Part I § Audit Log Domain Model)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

ActorKind = Literal["user", "guest", "system"]


class AuditOutcome(str, Enum):
    """Bounded-cardinality outcome — safe to use as a metric label."""

    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"


class AuditEvent(BaseModel):
    """One durable ``audit_events`` row."""

    id: uuid.UUID
    occurred_at: datetime
    actor_user_id: uuid.UUID | None = None
    actor_kind: ActorKind
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    outcome: AuditOutcome
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    trace_id: str | None = None
    source_ip_hash: str | None = None
    created_at: datetime | None = None
