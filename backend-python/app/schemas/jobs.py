"""Background Jobs REST API schemas (Epic 10 Phase 7).

Responses expose bounded job/schedule metadata only — never raw file bytes,
provider credentials, MCP secrets, filesystem paths, or full tool-argument
payloads. ``payload``/``result`` fields are filtered through an allowlist with
defensive redaction for accidental sensitive or oversized values.
"""

from __future__ import annotations

import datetime
import re
import uuid

from pydantic import BaseModel, Field

from app.ai.jobs.models import BackgroundJob, JobSchedule, JobStatus, ScheduleStatus

DEFAULT_JOBS_LIST_LIMIT = 50
MAX_JOBS_LIST_LIMIT = 100

_MAX_SCALAR_LEN = 256
_PAYLOAD_KEY_ALLOWLIST = frozenset({"version", "document_id", "user_id", "level"})
_RESULT_KEY_ALLOWLIST = frozenset({"summary", "counts"})
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(secret|password|token|credential|api[_-]?key|metadata|argument|content|bytes|file|path|tool)",
    re.IGNORECASE,
)

__all__ = [
    "DEFAULT_JOBS_LIST_LIMIT",
    "MAX_JOBS_LIST_LIMIT",
    "JobDetailResponse",
    "JobListResponse",
    "JobResponse",
    "JobRetryResponse",
    "JobScheduleListResponse",
    "JobScheduleResponse",
    "redact_job_payload",
    "redact_job_result",
    "redact_schedule_payload",
]


def _looks_like_secret_string(value: str) -> bool:
    if len(value) > _MAX_SCALAR_LEN:
        return True
    if "/" in value or "\\" in value:
        return True
    if value.startswith("eyJ") or value.startswith("sk-"):
        return True
    return False


def _is_safe_scalar(value: object) -> bool:
    if value is None or isinstance(value, (bool, int)):
        return True
    if isinstance(value, float):
        return True
    if isinstance(value, str):
        return not _looks_like_secret_string(value)
    return False


def _redact_allowlisted_object(
    data: dict[str, object] | None,
    *,
    allowlist: frozenset[str],
) -> dict[str, object]:
    if not data:
        return {}

    redacted: dict[str, object] = {}
    for key, value in data.items():
        if key not in allowlist or _SENSITIVE_KEY_PATTERN.search(key):
            continue
        if key == "counts":
            if isinstance(value, dict):
                redacted[key] = {
                    str(item_key): item_value
                    for item_key, item_value in value.items()
                    if type(item_value) is int
                    and not _SENSITIVE_KEY_PATTERN.search(str(item_key))
                }
            continue
        if _is_safe_scalar(value):
            redacted[key] = value
    return redacted


def redact_job_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a REST-safe subset of a job payload."""
    return _redact_allowlisted_object(payload, allowlist=_PAYLOAD_KEY_ALLOWLIST)


def redact_job_result(result: dict[str, object] | None) -> dict[str, object] | None:
    """Return a REST-safe subset of a job result, or ``None`` when absent."""
    if result is None:
        return None
    redacted = _redact_allowlisted_object(result, allowlist=_RESULT_KEY_ALLOWLIST)
    return redacted or None


def redact_schedule_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return a REST-safe subset of a schedule payload."""
    return redact_job_payload(payload)


class JobResponse(BaseModel):
    """One background job row for list/detail responses."""

    id: uuid.UUID
    job_type: str
    status: JobStatus
    payload: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    attempt_count: int
    max_attempts: int
    run_at: datetime.datetime
    last_error: str | None = None
    schedule_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None

    @classmethod
    def from_domain(cls, job: BackgroundJob) -> JobResponse:
        return cls(
            id=job.id,
            job_type=job.job_type,
            status=job.status,
            payload=redact_job_payload(job.payload),
            result=redact_job_result(job.result),
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            run_at=job.run_at,
            last_error=job.last_error,
            schedule_id=job.schedule_id,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class JobDetailResponse(JobResponse):
    """Detail view for one job (same shape as list items in V2)."""

    @classmethod
    def from_domain(cls, job: BackgroundJob) -> JobDetailResponse:
        base = JobResponse.from_domain(job)
        return cls.model_validate(base.model_dump())


class JobListResponse(BaseModel):
    """List returned by ``GET /api/jobs``."""

    jobs: list[JobResponse] = Field(default_factory=list)


class JobRetryResponse(BaseModel):
    """Updated job returned by ``POST /api/jobs/{id}/retry``."""

    job: JobResponse


class JobScheduleResponse(BaseModel):
    """One recurring schedule row."""

    id: uuid.UUID
    name: str
    job_type: str
    payload: dict[str, object] = Field(default_factory=dict)
    interval_seconds: int
    next_run_at: datetime.datetime
    status: ScheduleStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @classmethod
    def from_domain(cls, schedule: JobSchedule) -> JobScheduleResponse:
        return cls(
            id=schedule.id,
            name=schedule.name,
            job_type=schedule.job_type,
            payload=redact_schedule_payload(schedule.payload),
            interval_seconds=schedule.interval_seconds,
            next_run_at=schedule.next_run_at,
            status=schedule.status,
            created_at=schedule.created_at,
            updated_at=schedule.updated_at,
        )


class JobScheduleListResponse(BaseModel):
    """List returned by ``GET /api/jobs/schedules``."""

    schedules: list[JobScheduleResponse] = Field(default_factory=list)
