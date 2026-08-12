"""HITL orphaned snapshot sweep handler (Epic 10 Phase 3)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.agent.executor.agent_executor import AgentExecutor
from app.ai.hitl.service import AgentApprovalService
from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.jobs.models import BackgroundJob, JobResult
from app.core.config import Settings


async def hitl_orphaned_snapshot_sweep(
    job: BackgroundJob,
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    build_approval_service: Callable[[AsyncSession], AgentApprovalService],
    build_resume_executor: Callable[[AsyncSession], AgentExecutor],
) -> JobResult:
    """Resume or fail-safe crash-orphaned approved agent tool approvals."""
    if not settings.background_jobs_enabled:
        return JobResult(summary="background jobs disabled", counts={"scanned": 0})

    resumed = 0
    fail_safe = 0
    scanned = 0
    fail_safe_on_error = job.attempt_count >= job.max_attempts

    async with session_factory() as session:
        approval_store = AgentToolApprovalStore(
            session,
            client_audit_retention_days=settings.hitl_client_audit_retention_days,
        )
        orphans = await approval_store.list_orphaned_approved_snapshots(
            grace_seconds=settings.hitl_orphan_sweep_grace_seconds
        )
        scanned = len(orphans)
        if not orphans:
            return JobResult(
                summary="no orphaned snapshots",
                counts={"scanned": 0, "resumed": 0, "fail_safe": 0},
            )

        service = build_approval_service(session)
        executor = build_resume_executor(session)
        for approval in orphans:
            try:
                ok = await service.resume_orphaned_approval(
                    approval.id,
                    executor=executor,
                    fail_safe=fail_safe_on_error,
                )
            except Exception:
                if fail_safe_on_error:
                    fail_safe += 1
                    continue
                raise
            if ok:
                resumed += 1
            elif fail_safe_on_error:
                fail_safe += 1
        await session.commit()

    return JobResult(
        summary=f"resumed {resumed} orphaned approvals ({fail_safe} fail-safe)",
        counts={
            "scanned": scanned,
            "resumed": resumed,
            "fail_safe": fail_safe,
        },
    )
