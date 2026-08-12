"""HITL approval expiry sweep handler (Epic 10 Phase 3)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.jobs.models import BackgroundJob, JobResult
from app.ai.workflow.exceptions import WorkflowDecisionConflictError
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import ApprovalDecision
from app.ai.workflow.providers.postgres import PostgresWorkflowStore
from app.core.config import Settings
from app.db.chat import SqlChatStore


async def hitl_approval_expiry_sweep(
    job: BackgroundJob,
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    build_workflow_manager: Callable[[AsyncSession], WorkflowManager],
) -> JobResult:
    """Expire timed-out agent-tool and workflow-node approvals."""
    del job
    if not settings.background_jobs_enabled:
        return JobResult(summary="background jobs disabled", counts={"scanned": 0})

    agent_expired = 0
    workflow_expired = 0
    scanned = 0

    async with session_factory() as session:
        approval_store = AgentToolApprovalStore(
            session,
            client_audit_retention_days=settings.hitl_client_audit_retention_days,
        )
        chat_store = SqlChatStore(session)

        if settings.hitl_approval_timeout_hours > 0:
            pending = await approval_store.list_pending_past_timeout_hours(
                settings.hitl_approval_timeout_hours
            )
            scanned += len(pending)
            for approval in pending:
                expired = await approval_store.cas_expire_pending_sweep(approval.id)
                if expired is None:
                    continue
                agent_expired += 1
                if expired.pending_message_id is not None:
                    await chat_store.update_message(
                        expired.pending_message_id,
                        content="",
                        status="expired",
                        finish_reason="expired",
                        clear_pending_approval=True,
                    )
            await session.commit()

        if settings.workflow_approval_timeout_hours > 0:
            workflow_store = PostgresWorkflowStore(session, settings)
            stale = await workflow_store.list_stale_waiting_approval_executions(
                timeout_hours=settings.workflow_approval_timeout_hours
            )
            scanned += len(stale)
            if stale:
                manager = build_workflow_manager(session)
                for execution, run_id, owner_id in stale:
                    try:
                        await manager.apply_decision(
                            run_id,
                            execution.id,
                            owner_id=owner_id,
                            decision=ApprovalDecision.EXPIRED,
                            reason="Approval timed out.",
                        )
                    except WorkflowDecisionConflictError:
                        continue
                    workflow_expired += 1

    return JobResult(
        summary=(
            f"expired {agent_expired} agent approvals and "
            f"{workflow_expired} workflow approvals"
        ),
        counts={
            "scanned": scanned,
            "agent_expired": agent_expired,
            "workflow_expired": workflow_expired,
        },
    )
