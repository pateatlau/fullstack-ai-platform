"""Postgres persistence for agent tool-call approvals (Epic 09)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.hitl.exceptions import ApprovalDecisionConflictError, ApprovalNotFoundError
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalKind,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
)
from app.db.models import AgentToolApprovalRecord, ApprovalRevisionRecord


class AgentToolApprovalStore:
    """CRUD for ``agent_tool_approvals`` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        row = AgentToolApprovalRecord(
            session_id=session_id,
            owner_id=owner_id,
            execution_id=execution_id,
            approval_correlation_id=approval_correlation_id,
            status=ApprovalStatus.PENDING.value,
            proposed_calls=[call.model_dump(mode="json") for call in proposed_calls],
            paused_scratchpad=paused_scratchpad,
            paused_state=paused_state,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _to_domain(row)

    async def get(self, approval_id: uuid.UUID) -> AgentToolApproval | None:
        row = await self._session.get(AgentToolApprovalRecord, approval_id)
        if row is None:
            return None
        return _to_domain(row)

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        row = await self._session.scalar(
            select(AgentToolApprovalRecord).where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
            )
        )
        if row is None:
            return None
        return _to_domain(row)

    async def require_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval:
        approval = await self.get_for_owner(approval_id, owner_id=owner_id)
        if approval is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        return approval

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[AgentToolApproval]:
        stmt = select(AgentToolApprovalRecord).where(
            AgentToolApprovalRecord.owner_id == owner_id
        )
        if status is not None:
            stmt = stmt.where(AgentToolApprovalRecord.status == status.value)
        stmt = stmt.order_by(AgentToolApprovalRecord.requested_at.desc())
        rows = await self._session.scalars(stmt)
        return [_to_domain(row) for row in rows]

    async def list_for_session(
        self,
        session_id: uuid.UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[AgentToolApproval]:
        stmt = select(AgentToolApprovalRecord).where(
            AgentToolApprovalRecord.session_id == session_id
        )
        if status is not None:
            stmt = stmt.where(AgentToolApprovalRecord.status == status.value)
        stmt = stmt.order_by(AgentToolApprovalRecord.requested_at.desc())
        rows = await self._session.scalars(stmt)
        return [_to_domain(row) for row in rows]

    async def link_pending_message(
        self,
        approval_id: uuid.UUID,
        *,
        pending_message_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        stmt = (
            update(AgentToolApprovalRecord)
            .where(AgentToolApprovalRecord.id == approval_id)
            .values(
                pending_message_id=pending_message_id,
                updated_at=func.now(),
            )
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        await self._session.flush()
        return _to_domain(row)

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
        """Compare-and-swap ``pending`` → terminal decision status."""
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, object] = {
            "status": status.value,
            "decided_by": decided_by,
            "decided_at": now,
            "reason": reason,
            "updated_at": func.now(),
        }
        if edited_calls is not None:
            values["edited_calls"] = [
                call.model_dump(mode="json") for call in edited_calls
            ]

        stmt = (
            update(AgentToolApprovalRecord)
            .where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
                AgentToolApprovalRecord.status == ApprovalStatus.PENDING.value,
            )
            .values(**values)
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            existing = await self.get_for_owner(approval_id, owner_id=owner_id)
            if existing is None:
                raise ApprovalNotFoundError(
                    f"Approval {approval_id} not found or not owned by caller."
                )
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={existing.status.value})."
            )
        await self._session.flush()
        return _to_domain(row)

    async def cas_revise(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        edited_calls: list[ProposedToolCall],
    ) -> AgentToolApproval:
        """Update ``edited_calls`` while the approval remains ``pending``."""
        stmt = (
            update(AgentToolApprovalRecord)
            .where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
                AgentToolApprovalRecord.status == ApprovalStatus.PENDING.value,
            )
            .values(
                edited_calls=[call.model_dump(mode="json") for call in edited_calls],
                updated_at=func.now(),
            )
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            existing = await self.get_for_owner(approval_id, owner_id=owner_id)
            if existing is None:
                raise ApprovalNotFoundError(
                    f"Approval {approval_id} not found or not owned by caller."
                )
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={existing.status.value})."
            )
        await self._session.flush()
        return _to_domain(row)

    async def append_revision(
        self,
        *,
        approval_id: uuid.UUID,
        approval_kind: ApprovalKind,
        edited_by: uuid.UUID,
        edited_payload: list[ProposedToolCall] | dict[str, object],
        note: str | None = None,
    ) -> ApprovalRevision:
        max_number = await self._session.scalar(
            select(func.max(ApprovalRevisionRecord.revision_number)).where(
                ApprovalRevisionRecord.approval_id == approval_id,
                ApprovalRevisionRecord.approval_kind == approval_kind.value,
            )
        )
        revision_number = (max_number or 0) + 1
        payload_json: object
        if isinstance(edited_payload, list):
            payload_json = [call.model_dump(mode="json") for call in edited_payload]
        else:
            payload_json = edited_payload

        row = ApprovalRevisionRecord(
            approval_id=approval_id,
            approval_kind=approval_kind.value,
            revision_number=revision_number,
            edited_by=edited_by,
            edited_payload=payload_json,
            note=note,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _revision_to_domain(row)

    async def list_revisions(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> list[ApprovalRevision]:
        rows = await self._session.scalars(
            select(ApprovalRevisionRecord)
            .where(
                ApprovalRevisionRecord.approval_id == approval_id,
                ApprovalRevisionRecord.approval_kind == approval_kind.value,
            )
            .order_by(ApprovalRevisionRecord.revision_number.asc())
        )
        return [_revision_to_domain(row) for row in rows]


def _to_domain(row: AgentToolApprovalRecord) -> AgentToolApproval:
    proposed_raw = row.proposed_calls
    proposed_calls = [
        ProposedToolCall.model_validate(item)
        for item in (proposed_raw if isinstance(proposed_raw, list) else [])
    ]
    edited_raw = row.edited_calls
    edited_calls = (
        [ProposedToolCall.model_validate(item) for item in edited_raw]
        if isinstance(edited_raw, list)
        else None
    )
    return AgentToolApproval(
        id=row.id,
        session_id=row.session_id,
        owner_id=row.owner_id,
        execution_id=row.execution_id,
        approval_correlation_id=row.approval_correlation_id,
        status=ApprovalStatus(row.status),
        proposed_calls=proposed_calls,
        edited_calls=edited_calls,
        reason=row.reason,
        paused_scratchpad=[
            dict(item) if isinstance(item, dict) else {}
            for item in (row.paused_scratchpad or [])
        ],
        paused_state=dict(row.paused_state or {}),
        pending_message_id=row.pending_message_id,
        requested_at=row.requested_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _revision_to_domain(row: ApprovalRevisionRecord) -> ApprovalRevision:
    payload_raw = row.edited_payload
    if row.approval_kind == ApprovalKind.AGENT_TOOL.value and isinstance(
        payload_raw, list
    ):
        edited_payload: dict[str, object] | list[ProposedToolCall] = [
            ProposedToolCall.model_validate(item) for item in payload_raw
        ]
    elif isinstance(payload_raw, dict):
        edited_payload = dict(payload_raw)
    else:
        edited_payload = payload_raw  # type: ignore[assignment]

    return ApprovalRevision(
        id=row.id,
        approval_id=row.approval_id,
        approval_kind=ApprovalKind(row.approval_kind),
        revision_number=row.revision_number,
        edited_by=row.edited_by,
        edited_at=row.edited_at,
        edited_payload=edited_payload,
        note=row.note,
    )
