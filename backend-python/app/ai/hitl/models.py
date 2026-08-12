"""HITL domain models (stable public API after Phase 1)."""

from __future__ import annotations

import datetime
import enum
import uuid
from typing import Literal

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


class StageDecision(BaseModel):
    """One recorded step in a multi-stage approval checklist (recommendation #5).

    Stages are named labels supplied by the matching :class:`ApprovalRule`
    (``required_stages``). Any approval owner may currently satisfy any
    stage — enforcing that a specific *reviewer identity* holds the
    required role is deferred to Epic 11 RBAC.
    """

    stage: str
    decision: Literal["approved", "rejected"]
    decided_by: uuid.UUID
    decided_at: datetime.datetime
    reason: str | None = None
    comments: str | None = None


class RequestMetadata(BaseModel):
    """Optional caller/request context captured for audit purposes.

    ``source_ip`` and ``client_metadata`` are retained only while an approval
    remains ``pending`` and are redacted on every terminal transition. They are
    intentionally omitted from :class:`ApprovalAuditEntry` and must not be
    included in logs or REST response serialization.
    """

    request_id: str | None = None
    source_ip: str | None = None
    client_metadata: dict[str, object] = Field(default_factory=dict)


_TERMINAL_APPROVAL_STATUSES = frozenset(
    {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
    }
)


def redact_terminal_client_audit_fields(
    approval: AgentToolApproval,
) -> AgentToolApproval:
    """Strip client PII once an approval leaves the pending window."""
    if approval.status not in _TERMINAL_APPROVAL_STATUSES:
        return approval
    if approval.source_ip is None and not approval.client_metadata:
        return approval
    return approval.model_copy(update={"source_ip": None, "client_metadata": {}})


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
    comments: str | None = None
    paused_scratchpad: list[dict[str, object]]
    paused_state: dict[str, object]
    pending_message_id: uuid.UUID | None = None
    requested_at: datetime.datetime
    decided_at: datetime.datetime | None = None
    decided_by: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # Expiration (recommendation #3): ``None`` means the approval never
    # expires. Enforcement is lazy (checked on next touch) — see
    # ``AgentToolApprovalStore``; a proactive background sweep is Epic 10.
    expires_at: datetime.datetime | None = None
    # Audit metadata (recommendation #4). ``request_id`` survives terminal
    # transitions for correlation; ``source_ip`` and ``client_metadata`` are
    # pending-only (redacted on terminal transition — see
    # ``redact_terminal_client_audit_fields``) and excluded from
    # :class:`ApprovalAuditEntry`/REST responses.
    request_id: str | None = None
    source_ip: str | None = None
    client_metadata: dict[str, object] = Field(default_factory=dict)
    # Multi-stage approval checklist scaffold (recommendation #5).
    required_stages: list[str] = Field(default_factory=list)
    stage_decisions: list[StageDecision] = Field(default_factory=list)
    # Optimistic-locking version counter (recommendation #8).
    version: int = 1


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
    comments: str | None = None
    # Non-empty only while a multi-stage checklist (recommendation #5) still
    # has outstanding stages after this decision (status remains ``pending``).
    outstanding_stages: list[str] = Field(default_factory=list)


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
    comments: str | None = None
    edited: bool = False
    revision_count: int = 0
    decide_url: str
    expires_at: datetime.datetime | None = None
    required_stages: list[str] = Field(default_factory=list)
    stage_decisions: list[StageDecision] = Field(default_factory=list)


def redact_client_audit_fields(approval: AgentToolApproval) -> AgentToolApproval:
    """Remove pending client audit fields without changing approval status."""
    if approval.source_ip is None and not approval.client_metadata:
        return approval
    return approval.model_copy(update={"source_ip": None, "client_metadata": {}})


def client_audit_retention_expired(
    approval: AgentToolApproval,
    *,
    retention_days: int,
    now: datetime.datetime | None = None,
) -> bool:
    """True when persisted client audit fields exceed the configured retention window."""
    if retention_days <= 0:
        return False
    if approval.source_ip is None and not approval.client_metadata:
        return False
    observed_at = now or datetime.datetime.now(datetime.UTC)
    deadline = approval.requested_at + datetime.timedelta(days=retention_days)
    return deadline <= observed_at


def apply_client_audit_retention_policy(
    approval: AgentToolApproval,
    *,
    retention_days: int,
    now: datetime.datetime | None = None,
) -> AgentToolApproval:
    """Redact client audit fields on terminal transition or retention expiry."""
    approval = redact_terminal_client_audit_fields(approval)
    if approval.status is ApprovalStatus.PENDING and client_audit_retention_expired(
        approval,
        retention_days=retention_days,
        now=now,
    ):
        return redact_client_audit_fields(approval)
    return approval
