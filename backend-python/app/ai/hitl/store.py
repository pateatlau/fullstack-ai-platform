"""Postgres persistence for agent tool-call approvals (Epic 09)."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.hitl.exceptions import ApprovalDecisionConflictError, ApprovalNotFoundError
from app.ai.hitl.models import (
    AgentToolApproval,
    ApprovalAuditEntry,
    ApprovalKind,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
)
from app.ai.workflow.models import ApprovalDecision, NodeStatus, NodeType
from app.db.models import (
    AgentToolApprovalRecord,
    ApprovalRevisionRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
)


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
        await self._lock_revision_parent(approval_id, approval_kind=approval_kind)
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

    async def _lock_revision_parent(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> None:
        """Serialize concurrent revision writers for one approval (``FOR UPDATE``)."""
        if approval_kind == ApprovalKind.AGENT_TOOL:
            row = await self._session.scalar(
                select(AgentToolApprovalRecord)
                .where(AgentToolApprovalRecord.id == approval_id)
                .with_for_update()
            )
        elif approval_kind == ApprovalKind.WORKFLOW_NODE:
            row = await self._session.scalar(
                select(WorkflowNodeExecutionRecord)
                .where(WorkflowNodeExecutionRecord.id == approval_id)
                .with_for_update()
            )
        else:
            raise ValueError(f"Unsupported approval kind: {approval_kind.value}")

        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found for kind {approval_kind.value}."
            )

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


class ApprovalsStore:
    """Read-only aggregation across agent-tool and workflow-node approvals."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._agent_store = AgentToolApprovalStore(session)

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: ApprovalStatus | None = None,
        kind: ApprovalKind | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalAuditEntry], int]:
        entries: list[ApprovalAuditEntry] = []

        if kind is None or kind is ApprovalKind.AGENT_TOOL:
            agent_rows = await self._agent_store.list_for_owner(
                owner_id,
                status=status,
            )
            for approval in agent_rows:
                entries.append(_agent_audit_entry(approval, revision_count=0))

        if kind is None or kind is ApprovalKind.WORKFLOW_NODE:
            workflow_rows = await self._list_workflow_approvals_for_owner(
                owner_id,
                status=status,
            )
            for execution, run_id in workflow_rows:
                entries.append(
                    _workflow_audit_entry(execution, run_id=run_id, revision_count=0)
                )

        entries.sort(key=lambda item: item.requested_at, reverse=True)
        revision_counts = await self._revision_counts_for_entries(entries)
        entries = [
            entry.model_copy(
                update={
                    "revision_count": revision_counts.get((entry.id, entry.kind), 0)
                }
            )
            for entry in entries
        ]
        total = len(entries)
        page = entries[offset : offset + limit]
        return page, total

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> ApprovalAuditEntry | None:
        agent = await self._agent_store.get_for_owner(approval_id, owner_id=owner_id)
        if agent is not None:
            revision_count = await self._revision_count(
                approval_id,
                approval_kind=ApprovalKind.AGENT_TOOL,
            )
            return _agent_audit_entry(agent, revision_count=revision_count)

        workflow = await self._get_workflow_approval_for_owner(
            approval_id,
            owner_id=owner_id,
        )
        if workflow is None:
            return None
        execution, run_id = workflow
        revision_count = await self._revision_count(
            approval_id,
            approval_kind=ApprovalKind.WORKFLOW_NODE,
        )
        return _workflow_audit_entry(
            execution,
            run_id=run_id,
            revision_count=revision_count,
        )

    async def list_revisions_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> list[ApprovalRevision]:
        entry = await self.get_for_owner(approval_id, owner_id=owner_id)
        if entry is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        return await self._agent_store.list_revisions(
            approval_id,
            approval_kind=entry.kind,
        )

    async def count_pending(self) -> int:
        agent_pending = await self._session.scalar(
            select(func.count())
            .select_from(AgentToolApprovalRecord)
            .where(AgentToolApprovalRecord.status == ApprovalStatus.PENDING.value)
        )
        workflow_pending = await self._session.scalar(
            select(func.count())
            .select_from(WorkflowNodeExecutionRecord)
            .join(
                WorkflowRunRecord,
                WorkflowRunRecord.id == WorkflowNodeExecutionRecord.run_id,
            )
            .where(
                WorkflowNodeExecutionRecord.node_type == NodeType.APPROVAL.value,
                WorkflowNodeExecutionRecord.status == NodeStatus.WAITING_APPROVAL.value,
                WorkflowNodeExecutionRecord.decision.is_(None),
            )
        )
        return int(agent_pending or 0) + int(workflow_pending or 0)

    async def _list_workflow_approvals_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: ApprovalStatus | None,
    ) -> list[tuple[WorkflowNodeExecutionRecord, uuid.UUID]]:
        stmt = (
            select(WorkflowNodeExecutionRecord, WorkflowRunRecord.id)
            .join(
                WorkflowRunRecord,
                WorkflowRunRecord.id == WorkflowNodeExecutionRecord.run_id,
            )
            .where(
                WorkflowRunRecord.owner_id == owner_id,
                WorkflowNodeExecutionRecord.node_type == NodeType.APPROVAL.value,
            )
            .order_by(WorkflowNodeExecutionRecord.started_at.desc())
        )
        stmt = _apply_workflow_status_filter(stmt, status)
        rows = await self._session.execute(stmt)
        return [(execution, run_id) for execution, run_id in rows.all()]

    async def _get_workflow_approval_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> tuple[WorkflowNodeExecutionRecord, uuid.UUID] | None:
        row = await self._session.execute(
            select(WorkflowNodeExecutionRecord, WorkflowRunRecord.id)
            .join(
                WorkflowRunRecord,
                WorkflowRunRecord.id == WorkflowNodeExecutionRecord.run_id,
            )
            .where(
                WorkflowNodeExecutionRecord.id == approval_id,
                WorkflowRunRecord.owner_id == owner_id,
                WorkflowNodeExecutionRecord.node_type == NodeType.APPROVAL.value,
            )
        )
        result = row.one_or_none()
        if result is None:
            return None
        execution, run_id = result
        return execution, run_id

    async def _revision_count(
        self,
        approval_id: uuid.UUID,
        *,
        approval_kind: ApprovalKind,
    ) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(ApprovalRevisionRecord)
            .where(
                ApprovalRevisionRecord.approval_id == approval_id,
                ApprovalRevisionRecord.approval_kind == approval_kind.value,
            )
        )
        return int(count or 0)

    async def _revision_counts_for_entries(
        self,
        entries: list[ApprovalAuditEntry],
    ) -> dict[tuple[uuid.UUID, ApprovalKind], int]:
        if not entries:
            return {}
        keys = {(entry.id, entry.kind) for entry in entries}
        approval_ids = [entry.id for entry in entries]
        rows = await self._session.execute(
            select(
                ApprovalRevisionRecord.approval_id,
                ApprovalRevisionRecord.approval_kind,
                func.count(),
            )
            .where(ApprovalRevisionRecord.approval_id.in_(approval_ids))
            .group_by(
                ApprovalRevisionRecord.approval_id,
                ApprovalRevisionRecord.approval_kind,
            )
        )
        counts: dict[tuple[uuid.UUID, ApprovalKind], int] = {}
        for approval_id, approval_kind, count in rows.all():
            key = (approval_id, ApprovalKind(approval_kind))
            if key in keys:
                counts[key] = int(count)
        return counts


def _agent_decide_url(approval_id: uuid.UUID) -> str:
    return f"/api/approvals/{approval_id}/decide"


def _workflow_decide_url(*, run_id: uuid.UUID, node_execution_id: uuid.UUID) -> str:
    return f"/api/workflow-runs/{run_id}/nodes/{node_execution_id}/approve"


def _agent_audit_entry(
    approval: AgentToolApproval,
    *,
    revision_count: int,
) -> ApprovalAuditEntry:
    edited = approval.edited_calls is not None or revision_count > 0
    return ApprovalAuditEntry(
        id=approval.id,
        kind=ApprovalKind.AGENT_TOOL,
        approval_correlation_id=approval.approval_correlation_id,
        status=approval.status.value,
        tool_calls=list(approval.proposed_calls),
        session_id=approval.session_id,
        requested_at=approval.requested_at,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        decision=(
            approval.status.value
            if approval.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
            else None
        ),
        reason=approval.reason,
        edited=edited,
        revision_count=revision_count,
        decide_url=_agent_decide_url(approval.id),
    )


def _workflow_audit_entry(
    row: WorkflowNodeExecutionRecord,
    *,
    run_id: uuid.UUID,
    revision_count: int,
) -> ApprovalAuditEntry:
    edited = row.edited_arguments is not None or revision_count > 0
    decision = row.decision
    audit_status = _workflow_audit_status(row.status, decision)
    requested_at = (
        row.started_at or row.completed_at or datetime.datetime.now(datetime.UTC)
    )
    return ApprovalAuditEntry(
        id=row.id,
        kind=ApprovalKind.WORKFLOW_NODE,
        approval_correlation_id=row.id,
        status=audit_status,
        workflow_run_id=run_id,
        workflow_node_id=row.node_id,
        requested_at=requested_at,
        decided_at=row.decided_at,
        decided_by=row.decided_by,
        decision=decision,
        reason=row.reason,
        edited=edited,
        revision_count=revision_count,
        decide_url=_workflow_decide_url(run_id=run_id, node_execution_id=row.id),
    )


def _workflow_audit_status(status: str, decision: str | None) -> str:
    if decision == ApprovalDecision.APPROVED.value:
        return ApprovalStatus.APPROVED.value
    if decision == ApprovalDecision.REJECTED.value:
        return ApprovalStatus.REJECTED.value
    if status == NodeStatus.CANCELLED.value:
        return ApprovalStatus.CANCELLED.value
    if status == NodeStatus.WAITING_APPROVAL.value:
        return ApprovalStatus.PENDING.value
    return ApprovalStatus.PENDING.value


def _apply_workflow_status_filter(
    stmt,
    status: ApprovalStatus | None,
):
    if status is None:
        return stmt
    if status is ApprovalStatus.PENDING:
        return stmt.where(
            WorkflowNodeExecutionRecord.status == NodeStatus.WAITING_APPROVAL.value,
            WorkflowNodeExecutionRecord.decision.is_(None),
        )
    if status is ApprovalStatus.APPROVED:
        return stmt.where(
            WorkflowNodeExecutionRecord.decision == ApprovalDecision.APPROVED.value
        )
    if status is ApprovalStatus.REJECTED:
        return stmt.where(
            WorkflowNodeExecutionRecord.decision == ApprovalDecision.REJECTED.value
        )
    if status is ApprovalStatus.CANCELLED:
        return stmt.where(
            WorkflowNodeExecutionRecord.status == NodeStatus.CANCELLED.value
        )
    if status is ApprovalStatus.EXPIRED:
        return stmt.where(WorkflowNodeExecutionRecord.id.is_(None))
    return stmt


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
