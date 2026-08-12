"""Shared HITL test fakes (Epic 09)."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalAuditEntry,
    ApprovalKind,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
    RequestMetadata,
    StageDecision,
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
        expires_at: datetime.datetime | None = None,
        required_stages: list[str] | None = None,
        request_metadata: RequestMetadata | None = None,
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
            expires_at=expires_at,
            required_stages=list(required_stages or []),
            request_id=request_metadata.request_id if request_metadata else None,
            source_ip=request_metadata.source_ip if request_metadata else None,
            client_metadata=(
                request_metadata.client_metadata if request_metadata else {}
            ),
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
        if (
            row.status is ApprovalStatus.PENDING
            and row.expires_at is not None
            and row.expires_at <= datetime.datetime.now(datetime.UTC)
        ):
            expired = row.model_copy(
                update={
                    "status": ApprovalStatus.EXPIRED,
                    "version": row.version + 1,
                    "updated_at": datetime.datetime.now(datetime.UTC),
                }
            )
            self._replace(expired)
            return expired
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
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
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
        comments: str | None = None,
        edited_calls: list[ProposedToolCall] | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
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
            "comments": comments,
            "version": row.version + 1,
            "updated_at": now,
        }
        if edited_calls is not None:
            updates["edited_calls"] = edited_calls
        if request_metadata is not None:
            updates["request_id"] = request_metadata.request_id
            updates["source_ip"] = request_metadata.source_ip
            updates["client_metadata"] = request_metadata.client_metadata
        updated = row.model_copy(update=updates)
        self._replace(updated)
        return updated

    async def cas_cancel(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        reason: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval:
        row = await self.get_for_owner(approval_id, owner_id=owner_id)
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        now = datetime.datetime.now(datetime.UTC)
        updates: dict[str, object] = {
            "status": ApprovalStatus.CANCELLED,
            "decided_by": owner_id,
            "decided_at": now,
            "reason": reason,
            "version": row.version + 1,
            "updated_at": now,
        }
        if request_metadata is not None:
            updates["request_id"] = request_metadata.request_id
            updates["source_ip"] = request_metadata.source_ip
            updates["client_metadata"] = request_metadata.client_metadata
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
        if row.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{row.expires_at.isoformat() if row.expires_at else 'unknown'}."
            )
        if row.status != ApprovalStatus.PENDING:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status.value})."
            )
        updated = row.model_copy(
            update={
                "edited_calls": edited_calls,
                "version": row.version + 1,
                "updated_at": datetime.datetime.now(datetime.UTC),
            }
        )
        self._replace(updated)
        return updated

    async def append_stage_decision(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stage: str,
        decision: Literal["approved", "rejected"],
        decided_by: uuid.UUID,
        reason: str | None = None,
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
        entry = StageDecision(
            stage=stage,
            decision=decision,
            decided_by=decided_by,
            decided_at=datetime.datetime.now(datetime.UTC),
            reason=reason,
        )
        updated = row.model_copy(
            update={
                "stage_decisions": [*row.stage_decisions, entry],
                "version": row.version + 1,
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
        if await self.get(approval_id) is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found for kind {approval_kind.value}."
            )
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


class FakeApprovalsStore:
    """In-memory unified approvals read store for router tests."""

    def __init__(
        self,
        *,
        entries: list[ApprovalAuditEntry] | None = None,
        revisions: dict[uuid.UUID, list[ApprovalRevision]] | None = None,
        pending_count: int = 0,
    ) -> None:
        self.entries = list(entries or [])
        self.revisions = revisions or {}
        self.pending_count = pending_count
        self.last_list_kwargs: dict[str, object] | None = None

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: ApprovalStatus | None = None,
        kind: ApprovalKind | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalAuditEntry], int]:
        del owner_id
        self.last_list_kwargs = {
            "status": status,
            "kind": kind,
            "limit": limit,
            "offset": offset,
        }
        filtered = list(self.entries)
        if status is not None:
            filtered = [item for item in filtered if item.status == status.value]
        if kind is not None:
            filtered = [item for item in filtered if item.kind == kind]
        filtered.sort(key=lambda item: item.requested_at, reverse=True)
        total = len(filtered)
        return filtered[offset : offset + limit], total

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> ApprovalAuditEntry | None:
        del owner_id
        for entry in self.entries:
            if entry.id == approval_id:
                return entry
        return None

    async def list_revisions_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> list[ApprovalRevision]:
        if await self.get_for_owner(approval_id, owner_id=owner_id) is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        return sorted(
            self.revisions.get(approval_id, []),
            key=lambda item: item.revision_number,
        )

    async def count_pending(self) -> int:
        return self.pending_count
