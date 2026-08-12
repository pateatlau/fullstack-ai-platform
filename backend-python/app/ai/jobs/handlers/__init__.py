"""Register first-class job handlers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.deps import (
    build_agent_approval_service_for_session,
    build_hitl_resume_executor,
    build_workflow_manager_for_session,
)
from app.ai.jobs.handlers.hitl_expiry import hitl_approval_expiry_sweep
from app.ai.jobs.handlers.hitl_orphan_sweep import hitl_orphaned_snapshot_sweep
from app.ai.jobs.models import BackgroundJob, JobResult
from app.ai.jobs.registry import JobHandlerRegistry
from app.core.config import Settings

_FIRST_CLASS_JOB_TYPES: tuple[str, ...] = (
    "hitl_approval_expiry_sweep",
    "hitl_orphaned_snapshot_sweep",
    "workflow_run_retention_cleanup",
    "rag_document_indexing",
    "scheduled_evaluation_run",
)


async def _stub_handler(job: BackgroundJob) -> JobResult:
    return JobResult(summary=f"stub handler for {job.job_type}")


def register_all_handlers(
    registry: JobHandlerRegistry,
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Wire all first-class handlers before worker/scheduler startup."""

    async def _hitl_expiry(job: BackgroundJob) -> JobResult:
        return await hitl_approval_expiry_sweep(
            job,
            settings=settings,
            session_factory=session_factory,
            build_workflow_manager=lambda session: build_workflow_manager_for_session(
                session, settings
            ),
        )

    async def _hitl_orphan(job: BackgroundJob) -> JobResult:
        return await hitl_orphaned_snapshot_sweep(
            job,
            settings=settings,
            session_factory=session_factory,
            build_approval_service=lambda session: (
                build_agent_approval_service_for_session(session, settings)
            ),
            build_resume_executor=lambda session, service: build_hitl_resume_executor(
                settings,
                approval_service=service,
            ),
        )

    registry.register("hitl_approval_expiry_sweep", _hitl_expiry)
    registry.register("hitl_orphaned_snapshot_sweep", _hitl_orphan)

    for job_type in _FIRST_CLASS_JOB_TYPES:
        if job_type in {
            "hitl_approval_expiry_sweep",
            "hitl_orphaned_snapshot_sweep",
        }:
            continue
        registry.register(job_type, _stub_handler)
