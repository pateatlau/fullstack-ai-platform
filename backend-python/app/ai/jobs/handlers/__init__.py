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
from app.ai.jobs.handlers.rag_indexing import rag_document_indexing
from app.ai.jobs.handlers.scheduled_eval import scheduled_evaluation_run
from app.ai.jobs.handlers.security_audit_retention import (
    security_audit_retention_cleanup,
)
from app.ai.jobs.handlers.workflow_retention import workflow_run_retention_cleanup
from app.ai.jobs.models import BackgroundJob, JobResult
from app.ai.jobs.registry import JobHandlerRegistry
from app.core.config import Settings


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
                session=session,
            ),
        )

    async def _workflow_retention(job: BackgroundJob) -> JobResult:
        return await workflow_run_retention_cleanup(
            job,
            settings=settings,
            session_factory=session_factory,
        )

    async def _rag_indexing(job: BackgroundJob) -> JobResult:
        return await rag_document_indexing(
            job,
            settings=settings,
            session_factory=session_factory,
        )

    async def _scheduled_eval(job: BackgroundJob) -> JobResult:
        return await scheduled_evaluation_run(job, settings=settings)

    async def _security_audit_retention(job: BackgroundJob) -> JobResult:
        return await security_audit_retention_cleanup(
            job,
            settings=settings,
            session_factory=session_factory,
        )

    registry.register("hitl_approval_expiry_sweep", _hitl_expiry)
    registry.register("hitl_orphaned_snapshot_sweep", _hitl_orphan)
    registry.register("workflow_run_retention_cleanup", _workflow_retention)
    registry.register("rag_document_indexing", _rag_indexing)
    registry.register("scheduled_evaluation_run", _scheduled_eval)
    # Epic 11 Phase 3: sixth first-class handler, registered only when Security
    # & Governance is enabled (its schedule row also stays 'disabled' otherwise).
    if settings.security_governance_enabled:
        registry.register("security_audit_retention_cleanup", _security_audit_retention)
