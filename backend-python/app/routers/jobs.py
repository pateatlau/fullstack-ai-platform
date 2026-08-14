"""Authenticated Background Jobs REST API (Epic 10 Phase 7)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.ai.deps import (
    get_audit_logger,
    get_job_queue,
    get_job_schedule_store,
    get_rbac_service,
)
from app.ai.jobs.exceptions import JobNotFoundError
from app.ai.jobs.models import JobStatus
from app.ai.jobs.queue import JobQueue
from app.ai.jobs.schedule_store import JobScheduleStore
from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.logger import AuditLogger
from app.ai.security.audit.models import AuditOutcome
from app.ai.security.errors import SecurityErrorCode
from app.ai.security.rbac.permissions import PermissionKey
from app.ai.security.rbac.service import RbacService
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


async def _require_jobs_permission(
    *,
    settings: Settings,
    rbac_service: RbacService,
    caller: CallerContext,
    permission: PermissionKey,
) -> None:
    """Gate a Jobs REST endpoint on ``permission`` (Epic 11 Phase 2).

    No-op (any authenticated caller passes, Epic 10 behaviour) unless both
    ``security_governance_enabled`` and ``security_rbac_enforcement_enabled``
    are true.
    """
    if not (
        settings.security_governance_enabled
        and settings.security_rbac_enforcement_enabled
    ):
        return
    decision = await rbac_service.authorize(caller.user_id, permission)
    if not decision.allowed:
        raise AppError(
            code=SecurityErrorCode.PERMISSION_DENIED.value,
            message=f"Requires the '{permission.value}' permission.",
            status_code=403,
        )


@router.get("/api/jobs", response_model=JobListResponse)
async def list_jobs(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    queue: JobQueue = Depends(get_job_queue),
    rbac_service: RbacService = Depends(get_rbac_service),
    status: JobStatus | None = Query(default=None),
    job_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_JOBS_LIST_LIMIT, ge=1, le=MAX_JOBS_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> JobListResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id))
    await _require_jobs_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.JOBS_VIEW_ALL,
    )
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
    rbac_service: RbacService = Depends(get_rbac_service),
) -> JobScheduleListResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id))
    await _require_jobs_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.JOBS_VIEW_ALL,
    )
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
    rbac_service: RbacService = Depends(get_rbac_service),
) -> JobDetailResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id), job_id=str(job_id))
    await _require_jobs_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.JOBS_VIEW_ALL,
    )
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
    rbac_service: RbacService = Depends(get_rbac_service),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> JobRetryResponse:
    _require_background_jobs_enabled(settings)
    bind_context(user_id=str(caller.user_id), job_id=str(job_id))
    await _require_jobs_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.JOBS_RETRY,
    )
    retried = await queue.retry_dead_letter(job_id)
    if retried is not None:
        await audit_logger.record(
            actor=caller,
            action=AuditAction.JOB_RETRIED.value,
            outcome=AuditOutcome.SUCCESS,
            resource_type="job",
            resource_id=str(job_id),
        )
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
