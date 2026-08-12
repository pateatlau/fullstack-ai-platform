"""HITL approval REST schemas (Epic 09)."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.hitl.models import (
    ApprovalAuditEntry,
    ApprovalKind,
    ApprovalResult,
    ApprovalRevision,
    ProposedToolCall,
    StageDecision,
)

DEFAULT_APPROVALS_LIST_LIMIT = 50
MAX_APPROVALS_LIST_LIMIT = 100


class ApprovalReviseRequest(BaseModel):
    edited_calls: list[ProposedToolCall] = Field(min_length=1)
    note: str | None = None


class ApprovalDecideRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    edited_calls: list[ProposedToolCall] | None = None
    reason: str | None = None
    comments: str | None = None


class StageDecisionResponse(BaseModel):
    """One recorded multi-stage checklist step returned by the approvals API."""

    stage: str
    decision: Literal["approved", "rejected"]
    decided_by: uuid.UUID
    decided_at: datetime.datetime
    reason: str | None = None
    comments: str | None = None

    @classmethod
    def from_domain(cls, entry: StageDecision) -> StageDecisionResponse:
        return cls(
            stage=entry.stage,
            decision=entry.decision,
            decided_by=entry.decided_by,
            decided_at=entry.decided_at,
            reason=entry.reason,
            comments=entry.comments,
        )


class ApprovalCancelRequest(BaseModel):
    """Body for requester-initiated withdrawal (recommendation #2)."""

    reason: str | None = None


class ApprovalResultResponse(BaseModel):
    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    status: str
    edited: bool
    final_payload: dict[str, object] | list[ProposedToolCall] | None
    reason: str | None
    comments: str | None = None
    approver: uuid.UUID | None
    decided_at: datetime.datetime
    approval_correlation_id: uuid.UUID
    # Non-empty only while a multi-stage checklist (recommendation #5) still
    # has stages left to decide (status remains ``pending``).
    outstanding_stages: list[str] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, result: ApprovalResult) -> ApprovalResultResponse:
        return cls(
            approval_id=result.approval_id,
            approval_kind=result.approval_kind,
            status=result.status.value,
            edited=result.edited,
            final_payload=result.final_payload,
            reason=result.reason,
            comments=result.comments,
            approver=result.approver,
            decided_at=result.decided_at,
            approval_correlation_id=result.approval_correlation_id,
            outstanding_stages=result.outstanding_stages,
        )


class ApprovalAuditEntryResponse(BaseModel):
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
    stage_decisions: list[StageDecisionResponse] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, entry: ApprovalAuditEntry) -> ApprovalAuditEntryResponse:
        return cls(
            id=entry.id,
            kind=entry.kind,
            approval_correlation_id=entry.approval_correlation_id,
            status=entry.status,
            tool_calls=entry.tool_calls,
            workflow_run_id=entry.workflow_run_id,
            workflow_node_id=entry.workflow_node_id,
            session_id=entry.session_id,
            requested_at=entry.requested_at,
            decided_at=entry.decided_at,
            decided_by=entry.decided_by,
            decision=entry.decision,
            reason=entry.reason,
            comments=entry.comments,
            edited=entry.edited,
            revision_count=entry.revision_count,
            decide_url=entry.decide_url,
            expires_at=entry.expires_at,
            required_stages=entry.required_stages,
            stage_decisions=[
                StageDecisionResponse.from_domain(stage)
                for stage in entry.stage_decisions
            ],
        )


class ApprovalAuditListResponse(BaseModel):
    approvals: list[ApprovalAuditEntryResponse]
    limit: int
    offset: int
    total: int


class ApprovalRevisionResponse(BaseModel):
    id: uuid.UUID
    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    revision_number: int
    edited_by: uuid.UUID
    edited_at: datetime.datetime
    edited_payload: dict[str, object] | list[ProposedToolCall]
    note: str | None = None

    @classmethod
    def from_domain(cls, revision: ApprovalRevision) -> ApprovalRevisionResponse:
        return cls(
            id=revision.id,
            approval_id=revision.approval_id,
            approval_kind=revision.approval_kind,
            revision_number=revision.revision_number,
            edited_by=revision.edited_by,
            edited_at=revision.edited_at,
            edited_payload=revision.edited_payload,
            note=revision.note,
        )
