"""Postgres persistence for agent tool-call approvals (Epic 09)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.hitl.models import AgentToolApproval, ApprovalStatus, ProposedToolCall
from app.db.models import AgentToolApprovalRecord


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
            .values(pending_message_id=pending_message_id)
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        await self._session.flush()
        return _to_domain(row)


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
