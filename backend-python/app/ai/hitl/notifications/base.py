"""Outbound approval notification contracts (Epic 09 recommendation #6)."""

from __future__ import annotations

import datetime
import enum
import uuid
from typing import Protocol

from pydantic import BaseModel, Field

from app.ai.hitl.models import ApprovalKind


class ApprovalNotificationEventType(str, enum.Enum):
    """Lifecycle moments that fan out to notification providers."""

    REQUESTED = "requested"
    DECIDED = "decided"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ApprovalNotificationEvent(BaseModel):
    """Payload handed to every configured :class:`NotificationProvider`."""

    event_type: ApprovalNotificationEventType
    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    occurred_at: datetime.datetime
    summary: str
    metadata: dict[str, object] = Field(default_factory=dict)


class NotificationProvider(Protocol):
    """One outbound channel (webhook, Slack, email, ...)."""

    async def notify(self, event: ApprovalNotificationEvent) -> None: ...
