"""Background Jobs domain models (stable public API after Phase 1)."""

from __future__ import annotations

import datetime
import enum
import uuid

from pydantic import BaseModel, Field


class JobStatus(str, enum.Enum):
    """Lifecycle state of a background job row."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class ScheduleStatus(str, enum.Enum):
    """Lifecycle state of a recurring schedule row."""

    ENABLED = "enabled"
    DISABLED = "disabled"


class JobResult(BaseModel):
    """Uniform success summary returned by every job handler."""

    summary: str
    counts: dict[str, int] = Field(default_factory=dict)
    ref_id: str | None = None


class BackgroundJob(BaseModel):
    """Durable background job record."""

    id: uuid.UUID
    job_type: str
    status: JobStatus
    payload: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] | None = None
    attempt_count: int
    max_attempts: int
    version: int
    run_at: datetime.datetime
    locked_by: str | None = None
    locked_at: datetime.datetime | None = None
    last_error: str | None = None
    idempotency_key: str | None = None
    schedule_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class JobSchedule(BaseModel):
    """Recurring schedule that enqueues jobs on a fixed interval."""

    id: uuid.UUID
    name: str
    job_type: str
    payload: dict[str, object] = Field(default_factory=dict)
    interval_seconds: int
    next_run_at: datetime.datetime
    version: int
    status: ScheduleStatus
    created_at: datetime.datetime
    updated_at: datetime.datetime
