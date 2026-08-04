"""Workflow lifecycle domain events (Part I § Workflow Event Hooks).

Event payloads never include workflow input/output content — only identifiers
and operational metadata for future platform integrations.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class WorkflowEventType(StrEnum):
    """Canonical workflow lifecycle event types."""

    DEFINITION_CREATED = "workflow.definition.created"
    RUN_STARTED = "workflow.run.started"
    RUN_COMPLETED = "workflow.run.completed"
    RUN_FAILED = "workflow.run.failed"
    RUN_CANCELLED = "workflow.run.cancelled"
    NODE_STARTED = "workflow.node.started"
    NODE_COMPLETED = "workflow.node.completed"
    NODE_FAILED = "workflow.node.failed"
    APPROVAL_REQUESTED = "workflow.approval.requested"
    APPROVAL_DECIDED = "workflow.approval.decided"


class WorkflowEventMetadata(BaseModel):
    """Allowlisted operational fields — no workflow input/output content."""

    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    node_type: str | None = None
    previous_status: str | None = None


class WorkflowEvent(BaseModel):
    """Domain event base for workflow lifecycle notifications."""

    event_type: WorkflowEventType
    run_id: uuid.UUID | None = None
    definition_id: uuid.UUID | None = None
    owner_id: uuid.UUID
    status: str
    occurred_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    metadata: WorkflowEventMetadata = Field(default_factory=WorkflowEventMetadata)
