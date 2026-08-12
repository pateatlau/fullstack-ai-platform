"""Canonical workflow run and node-execution models (public API — stable after Phase 1)."""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from app.ai.workflow.models.context import WorkflowContext
from app.ai.workflow.models.definition import NodeType


class RunStatus(StrEnum):
    """Lifecycle status for a ``WorkflowRun``."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeStatus(StrEnum):
    """Lifecycle status for a ``WorkflowNodeExecution``."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ApprovalDecision(StrEnum):
    """Human approval outcome — stored on ``WorkflowNodeExecution.decision``."""

    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


APPROVAL_REQUESTED_AT_OUTPUT_KEY = "approval_requested_at"


class WorkflowRun(BaseModel):
    """A single execution instance of a workflow definition."""

    id: uuid.UUID
    workflow_definition_id: uuid.UUID
    owner_id: uuid.UUID
    idempotency_key: str = Field(min_length=1)
    session_id: uuid.UUID | None = None
    status: RunStatus
    context: WorkflowContext = Field(default_factory=WorkflowContext)
    current_node_ids: list[str] = Field(default_factory=list)
    checkpoint_version: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None

    @field_validator("idempotency_key")
    @classmethod
    def _strip_idempotency_key(cls, value: str) -> str:
        return normalize_idempotency_key(value)


def normalize_idempotency_key(value: str) -> str:
    """Strip and reject blank caller-supplied run idempotency keys."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("idempotency_key must not be blank.")
    return stripped


class WorkflowNodeExecution(BaseModel):
    """Persisted record of one node attempt within a run."""

    id: uuid.UUID
    run_id: uuid.UUID
    node_id: str = Field(min_length=1)
    node_type: NodeType
    attempt: int = Field(default=1, ge=1)
    status: NodeStatus
    input: dict[str, object] = Field(default_factory=dict)
    output: dict[str, object] | None = None
    error: str | None = None
    decided_by: uuid.UUID | None = None
    decided_at: datetime.datetime | None = None
    decision: ApprovalDecision | None = None
    edited_arguments: dict[str, object] | None = None
    reason: str | None = None
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None


def workflow_approval_requested_at(
    execution: WorkflowNodeExecution,
    *,
    run_created_at: datetime.datetime,
) -> datetime.datetime:
    """Return when the node entered ``waiting_approval`` (HITL request time)."""
    if isinstance(execution.output, dict):
        raw = execution.output.get(APPROVAL_REQUESTED_AT_OUTPUT_KEY)
        if isinstance(raw, str):
            parsed = datetime.datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=datetime.UTC)
            return parsed
    if execution.started_at is not None:
        return execution.started_at
    return run_created_at
