"""HITL domain models (stable public API after Phase 1)."""

from __future__ import annotations

import datetime
import enum
import uuid

from pydantic import BaseModel, Field


class ApprovalKind(str, enum.Enum):
    """Surface that produced an approval record."""

    AGENT_TOOL = "agent_tool"
    WORKFLOW_NODE = "workflow_node"


class ApprovalStatus(str, enum.Enum):
    """Lifecycle state of an approval record."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ProposedToolCall(BaseModel):
    """One planned tool invocation awaiting human review."""

    name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    call_id: str


class AgentToolApproval(BaseModel):
    """Persisted record of one paused chat/agent tool-call step."""

    id: uuid.UUID
    session_id: uuid.UUID
    owner_id: uuid.UUID
    execution_id: str
    approval_correlation_id: uuid.UUID
    status: ApprovalStatus
    proposed_calls: list[ProposedToolCall]
    edited_calls: list[ProposedToolCall] | None = None
    reason: str | None = None
    paused_scratchpad: list[dict[str, object]]
    paused_state: dict[str, object]
    pending_message_id: uuid.UUID | None = None
    requested_at: datetime.datetime
    decided_at: datetime.datetime | None = None
    decided_by: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ApprovalRevision(BaseModel):
    """One immutable edit submitted against a pending approval."""

    id: uuid.UUID
    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    revision_number: int
    edited_by: uuid.UUID
    edited_at: datetime.datetime
    edited_payload: dict[str, object] | list[ProposedToolCall]
    note: str | None = None


class ApprovalResult(BaseModel):
    """Canonical decision result returned by decide/apply_decision."""

    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    status: ApprovalStatus
    edited: bool
    final_payload: dict[str, object] | list[ProposedToolCall] | None
    reason: str | None
    approver: uuid.UUID | None = None
    decided_at: datetime.datetime
    approval_correlation_id: uuid.UUID


class ApprovalAuditEntry(BaseModel):
    """Unified read-model row for GET /api/approvals."""

    id: uuid.UUID
    kind: ApprovalKind
    approval_correlation_id: uuid.UUID
    status: str
    tool_calls: list[ProposedToolCall] | None = None
    workflow_run_id: uuid.UUID | None = None
    workflow_node_id: str | None = None
    session_id: uuid.UUID | None = None
    requested_at: datetime.datetime
    decided_at: datetime.datetime | None = None
    decided_by: uuid.UUID | None = None
    decision: str | None = None
    reason: str | None = None
    edited: bool = False
    revision_count: int = 0
    decide_url: str
