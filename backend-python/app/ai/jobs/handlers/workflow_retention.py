"""Workflow run and background job retention cleanup (Epic 10 Phase 4)."""

from __future__ import annotations

import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.jobs.models import BackgroundJob, JobResult
from app.core.config import Settings

_DELETE_BATCH_SIZE = 500


async def _delete_workflow_runs_batch(
    session: AsyncSession,
    *,
    cutoff: datetime.datetime,
    batch_size: int,
) -> int:
    result = await session.execute(
        text(
            """
            DELETE FROM workflow_runs
            WHERE id IN (
                SELECT id FROM workflow_runs
                WHERE status IN ('completed', 'failed', 'cancelled')
                  AND updated_at < :cutoff
                LIMIT :batch_size
            )
            """
        ),
        {"cutoff": cutoff, "batch_size": batch_size},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def _delete_background_jobs_batch(
    session: AsyncSession,
    *,
    cutoff: datetime.datetime,
    batch_size: int,
    exclude_job_id: object,
) -> int:
    result = await session.execute(
        text(
            """
            DELETE FROM background_jobs
            WHERE id IN (
                SELECT id FROM background_jobs
                WHERE status IN ('succeeded', 'failed', 'dead_letter', 'cancelled')
                  AND updated_at < :cutoff
                  AND id != :exclude_job_id
                LIMIT :batch_size
            )
            """
        ),
        {
            "cutoff": cutoff,
            "batch_size": batch_size,
            "exclude_job_id": exclude_job_id,
        },
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def workflow_run_retention_cleanup(
    job: BackgroundJob,
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> JobResult:
    """Purge terminal workflow runs and old background job rows past retention."""
    if not settings.background_jobs_enabled:
        return JobResult(
            summary="background jobs disabled",
            counts={"workflow_runs_deleted": 0, "background_jobs_deleted": 0},
        )

    now = datetime.datetime.now(datetime.UTC)
    workflow_cutoff = now - datetime.timedelta(
        days=settings.workflow_run_retention_days
    )
    jobs_cutoff = now - datetime.timedelta(days=settings.background_jobs_retention_days)

    workflow_runs_deleted = 0
    background_jobs_deleted = 0

    async with session_factory() as session:
        while True:
            deleted = await _delete_workflow_runs_batch(
                session,
                cutoff=workflow_cutoff,
                batch_size=_DELETE_BATCH_SIZE,
            )
            workflow_runs_deleted += deleted
            await session.commit()
            if deleted == 0:
                break

        while True:
            deleted = await _delete_background_jobs_batch(
                session,
                cutoff=jobs_cutoff,
                batch_size=_DELETE_BATCH_SIZE,
                exclude_job_id=job.id,
            )
            background_jobs_deleted += deleted
            await session.commit()
            if deleted == 0:
                break

    return JobResult(
        summary=(
            f"deleted {workflow_runs_deleted} workflow runs and "
            f"{background_jobs_deleted} background jobs"
        ),
        counts={
            "workflow_runs_deleted": workflow_runs_deleted,
            "background_jobs_deleted": background_jobs_deleted,
        },
    )
