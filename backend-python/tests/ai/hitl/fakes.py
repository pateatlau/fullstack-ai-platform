"""Shared HITL test fakes (Epic 09)."""

from __future__ import annotations

import datetime
import uuid

from app.ai.hitl.exceptions import ApprovalDecisionConflictError, ApprovalNotFoundError
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalKind,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
)


class InMemoryApprovalStore:
    """In-memory approval store with CAS/revision support for HITL tests."""

    def __init__(self) -> None:
        self.rows: list[AgentToolApproval] = []
        self.revisions: list[ApprovalRevision] = []

    async def create(
        self,
        *,
        session_id: uuid.UUID,
        owner_id: uuid.UUID,
        execution_id: str,
        approval_correlation_id: uuid.UUID,
        proposed_calls: list[ProposedToolCall],
        paused_scratchpad: list[dict[str, object]],
        paused_state: dict[str, object],
    ) -> AgentToolApproval:
        now = datetime.datetime.now(datetime.UTC)
        row = AgentToolApproval(
            id=uuid.uuid4(),
            session_id=session_id,
            owner_id=owner_id,
            execution_id=execution_id,
            approval_correlation_id=approval_correlation_id,
            status=ApprovalStatus.PENDING,
            proposed_calls=proposed_calls,
            edited_calls=None,
            reason=None,
            paused_scratchpad=paused_scratchpad,
            paused_state=paused_state,
            pending_message_id=None,
            requested_at=now,
            decided_at=None,
            decided_by=None,
            created_at=now,
            updated_at=now,
        )
        self.rows.append(row)
        return row

    async def get(self, approval_id: uuid.UUID) -> AgentToolApproval | None:
        return next((row for row in self.rows if row.id == approval_id), None)

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        row = await self.get(approval_id)
        if row is None or row.owner_id != owner_id:
            return None
        return row

    async def require_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        return row

    async def link_pending_message(
        self,
        approval_id: uuid.UUID,
        *,
        pending_message_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        row = await self.get(approval_id)
        if row is None:
            return None
        updated = row.model_copy(update={"pending_message_id": pending_message_id})
        self._replace(updated)
        return updated

    async def cas_decide(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        status: ApprovalStatus,
        decided_by: uuid.UUID,
        reason: str | None = None,
        edited_calls: list[ProposedToolCall] | None = None,
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        now = datetime.datetime.now(datetime.UTC)
        updates: dict[str, object] = {
            "status": status,
            "decided_by": decided_by,
            "decided_at": now,
            "reason": reason,
            "updated_at": now,
        }
        if edited_calls is not None:
            updates["edited_calls"] = edited_calls
        updated = row.model_copy(update=updates)
        self._replace(updated)
        return updated

    async def cas_revise(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        edited_calls: list[ProposedToolCall],
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        updated = row.model_copy(
            update={
                "edited_calls": edited_calls,
                "updated_at": datetime.datetime.now(datetime.UTC),
            }
        )
        self._replace(updated)
        return updated

    async def append_revision(
        self,
        *,
        approval_id: uuid.UUID,
        approval_kind: ApprovalKind,
        edited_by: uuid.UUID,
        edited_payload: list[ProposedToolCall] | dict[str, object],
        note: str | None = None,
    ) -> ApprovalRevision:
        matching = [
            item
            for item in self.revisions
            if item.approval_id == approval_id and item.approval_kind == approval_kind
        ]
        revision_number = len(matching) + 1
        revision = ApprovalRevision(
            id=uuid.uuid4(),
            approval_id=approval_id,
            approval_kind=approval_kind,
            revision_number=revision_number,
            edited_by=edited_by,
            edited_at=datetime.datetime.now(datetime.UTC),
            edited_payload=edited_payload,
            note=note,
        )
        self.revisions.append(revision)
        return revision

    async def list_revisions(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> list[ApprovalRevision]:
        return sorted(
            [
                item
                for item in self.revisions
                if item.approval_id == approval_id
                and item.approval_kind == approval_kind
            ],
            key=lambda item: item.revision_number,
        )

    def _replace(self, updated: AgentToolApproval) -> None:
        self.rows = [
            updated if existing.id == updated.id else existing for existing in self.rows
        ]
