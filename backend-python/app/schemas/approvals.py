"""HITL approval REST schemas (Epic 09)."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.hitl.models import (
    ApprovalKind,
    ApprovalResult,
    ApprovalRevision,
    ProposedToolCall,
)


class ApprovalReviseRequest(BaseModel):
    edited_calls: list[ProposedToolCall] = Field(min_length=1)
    note: str | None = None


class ApprovalDecideRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    edited_calls: list[ProposedToolCall] | None = None
    reason: str | None = None


class ApprovalResultResponse(BaseModel):
    approval_id: uuid.UUID
    approval_kind: ApprovalKind
    status: str
    edited: bool
    final_payload: dict[str, object] | list[ProposedToolCall] | None
    reason: str | None
    approver: uuid.UUID | None
    decided_at: datetime.datetime
    approval_correlation_id: uuid.UUID

    @classmethod
    def from_domain(cls, result: ApprovalResult) -> ApprovalResultResponse:
        return cls(
            approval_id=result.approval_id,
            approval_kind=result.approval_kind,
            status=result.status.value,
            edited=result.edited,
            final_payload=result.final_payload,
            reason=result.reason,
            approver=result.approver,
            decided_at=result.decided_at,
            approval_correlation_id=result.approval_correlation_id,
        )


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
