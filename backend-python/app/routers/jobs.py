"""Authenticated Background Jobs REST API (Epic 10 Phase 7)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.ai.deps import get_job_queue, get_job_schedule_store
from app.ai.jobs.exceptions import JobNotFoundError
from app.ai.jobs.models import JobStatus
from app.ai.jobs.queue import JobQueue
from app.ai.jobs.schedule_store import JobScheduleStore
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context
from app.schemas.jobs import (
    DEFAULT_JOBS_LIST_LIMIT,
    MAX_JOBS_LIST_LIMIT,
    JobDetailResponse,
    JobListResponse,
    JobResponse,
    JobRetryResponse,
    JobScheduleListResponse,
    JobScheduleResponse,
)

router = APIRouter()


def _require_background_jobs_enabled(settings: Settings) -> None:
    if not settings.background_jobs_enabled:
        raise AppError(
            code="feature_disabled",
            message="Background Jobs are not enabled on this server.",
            status_code=503,
        )


@router.get("/api/jobs", response_model=JobListResponse)
async def list_jobs(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
    status: JobStatus | None = Query(default=None),
    job_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_JOBS_LIST_LIMIT, ge=1, le=MAX_JOBS_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id))
    jobs = await queue.list(
        status=status,
        job_type=job_type,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(jobs=[JobResponse.from_domain(job) for job in jobs])


@router.get("/api/jobs/schedules", response_model=JobScheduleListResponse)
async def list_job_schedules(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    schedule_store: JobScheduleStore = Depends(get_job_schedule_store),
) -> JobScheduleListResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id))
    schedules = await schedule_store.list_all()
    return JobScheduleListResponse(
        schedules=[JobScheduleResponse.from_domain(schedule) for schedule in schedules]
    )


@router.get("/api/jobs/{job_id}", response_model=JobDetailResponse)
async def get_job_detail(
    job_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
) -> JobDetailResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id), job_id=str(job_id))
    job = await queue.get(job_id)
    if job is None:
        raise AppError(
            code="job_not_found",
            message=f"Job '{job_id}' was not found.",
            status_code=404,
        )
    return JobDetailResponse.from_domain(job)


@router.post("/api/jobs/{job_id}/retry", response_model=JobRetryResponse)
async def retry_dead_letter_job(
    job_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
) -> JobRetryResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id), job_id=str(job_id))
    retried = await queue.retry_dead_letter(job_id)
    if retried is not None:
        return JobRetryResponse(job=JobResponse.from_domain(retried))

    existing = await queue.get(job_id)
    if existing is None:
        raise AppError(
            code="job_not_found",
            message=f"Job '{job_id}' was not found.",
            status_code=404,
        ) from JobNotFoundError(str(job_id))

    raise AppError(
        code="job_not_retryable",
        message="Only dead-letter jobs can be manually retried.",
        status_code=409,
    )
