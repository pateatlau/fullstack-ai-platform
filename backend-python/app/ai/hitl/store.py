"""Postgres persistence for agent tool-call approvals (Epic 09)."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal, NoReturn

from sqlalchemy import and_, func, literal, or_, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

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
    apply_client_audit_retention_policy,
)
from app.ai.observability.metrics.instruments import (
    record_agent_tool_approval_pending_delta,
    record_approval_expired_metric,
)
from app.ai.workflow.models import ApprovalDecision, NodeStatus, NodeType
from app.ai.workflow.models.run import APPROVAL_REQUESTED_AT_OUTPUT_KEY
from app.db.models import (
    AgentToolApprovalRecord,
    ApprovalRevisionRecord,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
)


class AgentToolApprovalStore:
    """CRUD for ``agent_tool_approvals`` rows."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        client_audit_retention_days: int = 90,
    ) -> None:
        self._session = session
        self._client_audit_retention_days = client_audit_retention_days

    def _map_row(self, row: AgentToolApprovalRecord) -> AgentToolApproval:
        return _to_domain(
            row,
            client_audit_retention_days=self._client_audit_retention_days,
        )

    async def _map_row_persisting_retention(
        self, row: AgentToolApprovalRecord
    ) -> AgentToolApproval:
        had_client_audit = bool(row.source_ip or row.client_metadata)
        approval = self._map_row(row)
        if (
            had_client_audit
            and approval.source_ip is None
            and not approval.client_metadata
            and approval.status is ApprovalStatus.PENDING
        ):
            row.source_ip = None
            row.client_metadata = {}
            row.version = row.version + 1
            row.updated_at = func.now()
            await self._session.flush()
        return approval

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
        row = AgentToolApprovalRecord(
            session_id=session_id,
            owner_id=owner_id,
            execution_id=execution_id,
            approval_correlation_id=approval_correlation_id,
            status=ApprovalStatus.PENDING.value,
            proposed_calls=[call.model_dump(mode="json") for call in proposed_calls],
            paused_scratchpad=paused_scratchpad,
            paused_state=paused_state,
            expires_at=expires_at,
            required_stages=list(required_stages or []),
            request_id=request_metadata.request_id if request_metadata else None,
            source_ip=request_metadata.source_ip if request_metadata else None,
            client_metadata=(
                request_metadata.client_metadata if request_metadata else {}
            ),
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return self._map_row(row)

    async def get(self, approval_id: uuid.UUID) -> AgentToolApproval | None:
        row = await self._session.get(AgentToolApprovalRecord, approval_id)
        if row is None:
            return None
        return self._map_row(row)

    async def get_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        """Fetch one owned approval, lazily flipping it to ``expired`` if due.

        This is the single choke point for expiration (recommendation #3):
        every read/decide/revise/cancel path funnels through here (directly
        or via :meth:`require_for_owner`), so a stale ``pending`` row is
        transitioned the next time anyone touches it. There is no proactive
        background sweep for untouched rows (deferred to Epic 10).
        """
        row = await self._session.scalar(
            select(AgentToolApprovalRecord).where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
            )
        )
        if row is None:
            return None
        approval = await self._map_row_persisting_retention(row)
        if (
            approval.status is ApprovalStatus.PENDING
            and approval.expires_at is not None
        ):
            # Always attempt the DB-side CAS; ``expires_at <= now()`` uses DB
            # time so expiry is enforced even when the app server clock lags.
            expired = await self._expire_if_due(approval_id, owner_id=owner_id)
            if expired is not None:
                return expired
        return approval

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
        if approval.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{approval.expires_at.isoformat() if approval.expires_at else 'unknown'}."
            )
        return approval

    async def _expire_if_due(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        """Compare-and-swap ``pending`` → ``expired`` when the deadline has passed."""
        stmt = (
            update(AgentToolApprovalRecord)
            .where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
                AgentToolApprovalRecord.status == ApprovalStatus.PENDING.value,
                AgentToolApprovalRecord.expires_at.is_not(None),
                AgentToolApprovalRecord.expires_at <= func.now(),
            )
            .values(
                status=ApprovalStatus.EXPIRED.value,
                source_ip=None,
                client_metadata={},
                version=AgentToolApprovalRecord.version + 1,
                updated_at=func.now(),
            )
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        await self._session.flush()
        record_agent_tool_approval_pending_delta(-1)
        record_approval_expired_metric(kind=ApprovalKind.AGENT_TOOL.value)
        return self._map_row(row)

    async def _resolve_cas_miss(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> NoReturn:
        """Raise the precise HITL error a failed CAS write should surface."""
        existing = await self.get_for_owner(approval_id, owner_id=owner_id)
        if existing is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if existing.status is ApprovalStatus.EXPIRED:
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at "
                f"{existing.expires_at.isoformat() if existing.expires_at else 'unknown'}."
            )
        raise ApprovalDecisionConflictError(
            f"Approval {approval_id} is no longer pending (status={existing.status.value})."
        )

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
        return [self._map_row(row) for row in rows]

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
        return [self._map_row(row) for row in rows]

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
        return self._map_row(row)

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
        """Compare-and-swap ``pending`` → terminal decision status."""
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, object] = {
            "status": status.value,
            "decided_by": decided_by,
            "decided_at": now,
            "reason": reason,
            "comments": comments,
            "version": AgentToolApprovalRecord.version + 1,
            "updated_at": func.now(),
        }
        if edited_calls is not None:
            values["edited_calls"] = [
                call.model_dump(mode="json") for call in edited_calls
            ]
        values["source_ip"] = None
        values["client_metadata"] = {}
        if request_metadata is not None:
            values["request_id"] = request_metadata.request_id

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
            await self._resolve_cas_miss(approval_id, owner_id=owner_id)
            raise AssertionError("unreachable: _resolve_cas_miss always raises")
        await self._session.flush()
        return self._map_row(row)

    async def cas_cancel(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        reason: str | None = None,
        request_metadata: RequestMetadata | None = None,
    ) -> AgentToolApproval:
        """Compare-and-swap ``pending`` → ``cancelled`` (requester withdrawal)."""
        now = datetime.datetime.now(datetime.UTC)
        values: dict[str, object] = {
            "status": ApprovalStatus.CANCELLED.value,
            "decided_by": owner_id,
            "decided_at": now,
            "reason": reason,
            "version": AgentToolApprovalRecord.version + 1,
            "updated_at": func.now(),
            "source_ip": None,
            "client_metadata": {},
        }
        if request_metadata is not None:
            values["request_id"] = request_metadata.request_id

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
            await self._resolve_cas_miss(approval_id, owner_id=owner_id)
            raise AssertionError("unreachable: _resolve_cas_miss always raises")
        await self._session.flush()
        return self._map_row(row)

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
                version=AgentToolApprovalRecord.version + 1,
                updated_at=func.now(),
            )
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            await self._resolve_cas_miss(approval_id, owner_id=owner_id)
            raise AssertionError("unreachable: _resolve_cas_miss always raises")
        await self._session.flush()
        return self._map_row(row)

    async def append_stage_decision(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stage: str,
        decision: Literal["approved", "rejected"],
        decided_by: uuid.UUID,
        reason: str | None = None,
        comments: str | None = None,
    ) -> AgentToolApproval:
        """Append one multi-stage checklist entry while the approval stays ``pending``.

        Locks the row (``FOR UPDATE``) rather than a bare CAS because the
        update is a JSONB list append, not a single-column swap.
        """
        row = await self._session.scalar(
            select(AgentToolApprovalRecord)
            .where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ApprovalNotFoundError(
                f"Approval {approval_id} not found or not owned by caller."
            )
        if row.status == ApprovalStatus.PENDING.value and row.expires_at is not None:
            expired = await self._expire_if_due(approval_id, owner_id=owner_id)
            if expired is not None:
                raise ApprovalExpiredError(
                    f"Approval {approval_id} expired at "
                    f"{expired.expires_at.isoformat() if expired.expires_at else 'unknown'}."
                )
        if row.status == ApprovalStatus.EXPIRED.value:
            expired_at = row.expires_at.isoformat() if row.expires_at else "unknown"
            raise ApprovalExpiredError(
                f"Approval {approval_id} expired at {expired_at}."
            )
        if row.status != ApprovalStatus.PENDING.value:
            raise ApprovalDecisionConflictError(
                f"Approval {approval_id} is no longer pending (status={row.status})."
            )
        entry = StageDecision(
            stage=stage,
            decision=decision,
            decided_by=decided_by,
            decided_at=datetime.datetime.now(datetime.UTC),
            reason=reason,
            comments=comments,
        )
        row.stage_decisions = [
            *(row.stage_decisions or []),
            entry.model_dump(mode="json"),
        ]
        row.version = row.version + 1
        row.updated_at = func.now()
        await self._session.flush()
        await self._session.refresh(row)
        return self._map_row(row)

    async def rollback_last_stage_decision(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        stage: str,
        decision: Literal["approved", "rejected"],
    ) -> None:
        """Remove the last appended stage entry when a follow-up CAS fails."""
        row = await self._session.scalar(
            select(AgentToolApprovalRecord)
            .where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.owner_id == owner_id,
            )
            .with_for_update()
        )
        if row is None or row.status != ApprovalStatus.PENDING.value:
            return
        decisions_raw = list(row.stage_decisions or [])
        if not decisions_raw:
            return
        last = StageDecision.model_validate(decisions_raw[-1])
        if last.stage != stage or last.decision != decision:
            return
        row.stage_decisions = decisions_raw[:-1]
        row.version = row.version + 1
        row.updated_at = func.now()
        await self._session.flush()

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

    async def list_pending_past_timeout_hours(
        self,
        timeout_hours: int,
    ) -> list[AgentToolApproval]:
        """Return pending approvals older than ``timeout_hours`` since request."""
        cutoff = func.now() - datetime.timedelta(hours=timeout_hours)
        rows = await self._session.scalars(
            select(AgentToolApprovalRecord).where(
                AgentToolApprovalRecord.status == ApprovalStatus.PENDING.value,
                AgentToolApprovalRecord.requested_at < cutoff,
            )
        )
        return [self._map_row(row) for row in rows]

    async def cas_expire_pending_sweep(
        self,
        approval_id: uuid.UUID,
    ) -> AgentToolApproval | None:
        """Compare-and-swap ``pending`` → ``expired`` for background sweeps."""
        stmt = (
            update(AgentToolApprovalRecord)
            .where(
                AgentToolApprovalRecord.id == approval_id,
                AgentToolApprovalRecord.status == ApprovalStatus.PENDING.value,
            )
            .values(
                status=ApprovalStatus.EXPIRED.value,
                paused_scratchpad=[],
                paused_state={},
                source_ip=None,
                client_metadata={},
                version=AgentToolApprovalRecord.version + 1,
                updated_at=func.now(),
            )
            .returning(AgentToolApprovalRecord)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        await self._session.flush()
        record_agent_tool_approval_pending_delta(-1)
        record_approval_expired_metric(kind=ApprovalKind.AGENT_TOOL.value)
        return self._map_row(row)

    async def list_orphaned_approved_snapshots(
        self,
        *,
        grace_seconds: int,
    ) -> list[AgentToolApproval]:
        """Approved rows whose pause snapshot survived past the grace window."""
        cutoff = func.now() - datetime.timedelta(seconds=grace_seconds)
        rows = await self._session.scalars(
            select(AgentToolApprovalRecord).where(
                AgentToolApprovalRecord.status == ApprovalStatus.APPROVED.value,
                AgentToolApprovalRecord.decided_at.is_not(None),
                AgentToolApprovalRecord.decided_at < cutoff,
            )
        )
        return [self._map_row(row) for row in rows if _has_pause_snapshot_row(row)]

    async def clear_pause_snapshot(self, approval_id: uuid.UUID) -> None:
        """Drop persisted pause payload once resume or fail-safe completes."""
        stmt = (
            update(AgentToolApprovalRecord)
            .where(AgentToolApprovalRecord.id == approval_id)
            .values(
                paused_scratchpad=[],
                paused_state={},
                version=AgentToolApprovalRecord.version + 1,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()


class ApprovalsStore:
    """Read-only aggregation across agent-tool and workflow-node approvals."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        client_audit_retention_days: int = 90,
    ) -> None:
        self._session = session
        self._agent_store = AgentToolApprovalStore(
            session,
            client_audit_retention_days=client_audit_retention_days,
        )

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        *,
        status: ApprovalStatus | None = None,
        kind: ApprovalKind | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalAuditEntry], int]:
        listing = _audit_listing_subquery(
            owner_id,
            status=status,
            kind=kind,
        )
        total = int(
            await self._session.scalar(select(func.count()).select_from(listing)) or 0
        )
        if total == 0:
            return [], 0

        page_rows = (
            await self._session.execute(
                select(
                    listing.c.approval_id,
                    listing.c.approval_kind,
                )
                .order_by(listing.c.requested_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        if not page_rows:
            return [], total

        agent_ids = [
            row.approval_id
            for row in page_rows
            if row.approval_kind == ApprovalKind.AGENT_TOOL.value
        ]
        workflow_ids = [
            row.approval_id
            for row in page_rows
            if row.approval_kind == ApprovalKind.WORKFLOW_NODE.value
        ]

        agents_by_id: dict[uuid.UUID, AgentToolApproval] = {}
        if agent_ids:
            agent_rows = await self._session.scalars(
                select(AgentToolApprovalRecord).where(
                    AgentToolApprovalRecord.id.in_(agent_ids),
                    AgentToolApprovalRecord.owner_id == owner_id,
                )
            )
            agents_by_id = {
                row.id: self._agent_store._map_row(row) for row in agent_rows
            }

        workflows_by_id: dict[
            uuid.UUID, tuple[WorkflowNodeExecutionRecord, uuid.UUID, datetime.datetime]
        ] = {}
        if workflow_ids:
            workflow_rows = await self._session.execute(
                select(
                    WorkflowNodeExecutionRecord,
                    WorkflowRunRecord.id,
                    WorkflowRunRecord.created_at,
                )
                .join(
                    WorkflowRunRecord,
                    WorkflowRunRecord.id == WorkflowNodeExecutionRecord.run_id,
                )
                .where(
                    WorkflowNodeExecutionRecord.id.in_(workflow_ids),
                    WorkflowRunRecord.owner_id == owner_id,
                    WorkflowNodeExecutionRecord.node_type == NodeType.APPROVAL.value,
                )
            )
            workflows_by_id = {
                execution.id: (execution, run_id, run_created_at)
                for execution, run_id, run_created_at in workflow_rows.all()
            }

        entries: list[ApprovalAuditEntry] = []
        for row in page_rows:
            if row.approval_kind == ApprovalKind.AGENT_TOOL.value:
                approval = agents_by_id.get(row.approval_id)
                if approval is not None:
                    entries.append(_agent_audit_entry(approval, revision_count=0))
                continue
            workflow = workflows_by_id.get(row.approval_id)
            if workflow is not None:
                execution, run_id, run_created_at = workflow
                entries.append(
                    _workflow_audit_entry(
                        execution,
                        run_id=run_id,
                        run_created_at=run_created_at,
                        revision_count=0,
                    )
                )

        revision_counts = await self._revision_counts_for_entries(entries)
        page = [
            entry.model_copy(
                update={
                    "revision_count": revision_counts.get((entry.id, entry.kind), 0)
                }
            )
            for entry in entries
        ]
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
        execution, run_id, run_created_at = workflow
        revision_count = await self._revision_count(
            approval_id,
            approval_kind=ApprovalKind.WORKFLOW_NODE,
        )
        return _workflow_audit_entry(
            execution,
            run_id=run_id,
            run_created_at=run_created_at,
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
                _workflow_pending_audit_predicate(),
            )
        )
        return int(agent_pending or 0) + int(workflow_pending or 0)

    async def _get_workflow_approval_for_owner(
        self,
        approval_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
    ) -> tuple[WorkflowNodeExecutionRecord, uuid.UUID, datetime.datetime] | None:
        row = await self._session.execute(
            select(
                WorkflowNodeExecutionRecord,
                WorkflowRunRecord.id,
                WorkflowRunRecord.created_at,
            )
            .join(
                WorkflowRunRecord,
                WorkflowRunRecord.id == WorkflowNodeExecutionRecord.run_id,
            )
            .where(
                WorkflowNodeExecutionRecord.id == approval_id,
                WorkflowRunRecord.owner_id == owner_id,
                WorkflowNodeExecutionRecord.node_type == NodeType.APPROVAL.value,
                _workflow_inbox_predicate(),
            )
        )
        result = row.one_or_none()
        if result is None:
            return None
        execution, run_id, run_created_at = result
        return execution, run_id, run_created_at

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
        comments=approval.comments,
        edited=edited,
        revision_count=revision_count,
        decide_url=_agent_decide_url(approval.id),
        expires_at=approval.expires_at,
        required_stages=list(approval.required_stages),
        stage_decisions=list(approval.stage_decisions),
    )


def _workflow_requested_at(
    row: WorkflowNodeExecutionRecord,
    *,
    run_created_at: datetime.datetime,
) -> datetime.datetime:
    if isinstance(row.output, dict):
        raw = row.output.get(APPROVAL_REQUESTED_AT_OUTPUT_KEY)
        if isinstance(raw, str):
            parsed = datetime.datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=datetime.UTC)
            return parsed
    return row.started_at or row.completed_at or run_created_at


def _workflow_audit_entry(
    row: WorkflowNodeExecutionRecord,
    *,
    run_id: uuid.UUID,
    run_created_at: datetime.datetime,
    revision_count: int,
) -> ApprovalAuditEntry:
    edited = row.edited_arguments is not None or revision_count > 0
    decision = row.decision
    audit_status = _workflow_audit_status(row.status, decision)
    requested_at = _workflow_requested_at(row, run_created_at=run_created_at)
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
    """Map workflow node execution state to public ``ApprovalStatus`` audit values."""
    if decision == ApprovalDecision.APPROVED.value:
        return ApprovalStatus.APPROVED.value
    if decision == ApprovalDecision.REJECTED.value:
        return ApprovalStatus.REJECTED.value
    if decision == ApprovalDecision.EXPIRED.value:
        return ApprovalStatus.EXPIRED.value
    if status == NodeStatus.CANCELLED.value:
        return ApprovalStatus.CANCELLED.value
    if status == NodeStatus.WAITING_APPROVAL.value and decision is None:
        return ApprovalStatus.PENDING.value
    raise ValueError(
        f"Workflow approval node {status!r} with decision={decision!r} "
        "is not eligible for the HITL audit inbox."
    )


def _workflow_pending_audit_predicate():
    return and_(
        WorkflowNodeExecutionRecord.status == NodeStatus.WAITING_APPROVAL.value,
        WorkflowNodeExecutionRecord.decision.is_(None),
    )


def _workflow_approved_audit_predicate():
    return WorkflowNodeExecutionRecord.decision == ApprovalDecision.APPROVED.value


def _workflow_rejected_audit_predicate():
    return WorkflowNodeExecutionRecord.decision == ApprovalDecision.REJECTED.value


def _workflow_cancelled_audit_predicate():
    return WorkflowNodeExecutionRecord.status == NodeStatus.CANCELLED.value


def _workflow_inbox_predicate():
    """Approval nodes that entered the HITL audit lifecycle (excludes pre-pause states)."""
    return or_(
        _workflow_pending_audit_predicate(),
        _workflow_approved_audit_predicate(),
        _workflow_rejected_audit_predicate(),
        _workflow_cancelled_audit_predicate(),
    )


def _apply_workflow_status_filter(
    stmt,
    status: ApprovalStatus | None,
):
    if status is None:
        return stmt
    if status is ApprovalStatus.PENDING:
        return stmt.where(_workflow_pending_audit_predicate())
    if status is ApprovalStatus.APPROVED:
        return stmt.where(_workflow_approved_audit_predicate())
    if status is ApprovalStatus.REJECTED:
        return stmt.where(_workflow_rejected_audit_predicate())
    if status is ApprovalStatus.CANCELLED:
        return stmt.where(_workflow_cancelled_audit_predicate())
    if status is ApprovalStatus.EXPIRED:
        return stmt.where(
            WorkflowNodeExecutionRecord.decision == ApprovalDecision.EXPIRED.value
        )
    return stmt


def _apply_agent_status_filter(
    stmt,
    status: ApprovalStatus | None,
):
    if status is None:
        return stmt
    return stmt.where(AgentToolApprovalRecord.status == status.value)


def _agent_audit_listing_select(
    owner_id: uuid.UUID,
    *,
    status: ApprovalStatus | None,
):
    stmt = select(
        AgentToolApprovalRecord.id.label("approval_id"),
        literal(ApprovalKind.AGENT_TOOL.value).label("approval_kind"),
        AgentToolApprovalRecord.requested_at.label("requested_at"),
    ).where(AgentToolApprovalRecord.owner_id == owner_id)
    return _apply_agent_status_filter(stmt, status)


def _workflow_audit_listing_select(
    owner_id: uuid.UUID,
    *,
    status: ApprovalStatus | None,
):
    stmt = (
        select(
            WorkflowNodeExecutionRecord.id.label("approval_id"),
            literal(ApprovalKind.WORKFLOW_NODE.value).label("approval_kind"),
            func.coalesce(
                WorkflowNodeExecutionRecord.started_at,
                WorkflowNodeExecutionRecord.completed_at,
                WorkflowRunRecord.created_at,
            ).label("requested_at"),
        )
        .join(
            WorkflowRunRecord,
            WorkflowRunRecord.id == WorkflowNodeExecutionRecord.run_id,
        )
        .where(
            WorkflowRunRecord.owner_id == owner_id,
            WorkflowNodeExecutionRecord.node_type == NodeType.APPROVAL.value,
            _workflow_inbox_predicate(),
        )
    )
    return _apply_workflow_status_filter(stmt, status)


def _audit_listing_subquery(
    owner_id: uuid.UUID,
    *,
    status: ApprovalStatus | None,
    kind: ApprovalKind | None,
):
    if kind is ApprovalKind.AGENT_TOOL:
        return _agent_audit_listing_select(owner_id, status=status).subquery()
    if kind is ApprovalKind.WORKFLOW_NODE:
        return _workflow_audit_listing_select(owner_id, status=status).subquery()
    return union_all(
        _agent_audit_listing_select(owner_id, status=status),
        _workflow_audit_listing_select(owner_id, status=status),
    ).subquery()


def _has_pause_snapshot_row(row: AgentToolApprovalRecord) -> bool:
    return bool(row.paused_scratchpad) or bool(row.paused_state)


def _to_domain(
    row: AgentToolApprovalRecord,
    *,
    client_audit_retention_days: int = 0,
) -> AgentToolApproval:
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
    stage_decisions_raw = row.stage_decisions
    approval = AgentToolApproval(
        id=row.id,
        session_id=row.session_id,
        owner_id=row.owner_id,
        execution_id=row.execution_id,
        approval_correlation_id=row.approval_correlation_id,
        status=ApprovalStatus(row.status),
        proposed_calls=proposed_calls,
        edited_calls=edited_calls,
        reason=row.reason,
        comments=row.comments,
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
        expires_at=row.expires_at,
        request_id=row.request_id,
        source_ip=row.source_ip,
        client_metadata=dict(row.client_metadata or {}),
        required_stages=[str(item) for item in (row.required_stages or [])],
        stage_decisions=[
            StageDecision.model_validate(item)
            for item in (
                stage_decisions_raw if isinstance(stage_decisions_raw, list) else []
            )
        ],
        version=row.version,
    )
    return apply_client_audit_retention_policy(
        approval,
        retention_days=client_audit_retention_days,
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
