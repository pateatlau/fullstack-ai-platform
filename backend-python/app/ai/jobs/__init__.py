"""Background Jobs platform (Epic 10).

Public API is stable after Phase 1 — see epic Part I § Public APIs.
"""

from app.ai.jobs.exceptions import (
    JobConcurrencyError,
    JobHandlerNotFoundError,
    JobNotFoundError,
    JobsError,
    ScheduleNotFoundError,
)
from app.ai.jobs.models import (
    BackgroundJob,
    JobResult,
    JobSchedule,
    JobStatus,
    ScheduleStatus,
)
from app.ai.jobs.queue import JobQueue, PostgresJobQueue, generate_worker_id
from app.ai.jobs.registry import JobHandler, JobHandlerRegistry
from app.ai.jobs.retry import NonRetryableJobError, compute_backoff_seconds
from app.ai.jobs.schedule_store import JobScheduleStore, PostgresJobScheduleStore
from app.ai.jobs.scheduler import JobScheduler, compute_advanced_next_run_at
from app.ai.jobs.worker import JobWorker

__all__ = [
    "BackgroundJob",
    "JobConcurrencyError",
    "JobHandler",
    "JobHandlerNotFoundError",
    "JobHandlerRegistry",
    "JobNotFoundError",
    "JobQueue",
    "JobResult",
    "JobSchedule",
    "JobScheduleStore",
    "JobScheduler",
    "JobStatus",
    "JobWorker",
    "JobsError",
    "NonRetryableJobError",
    "PostgresJobQueue",
    "PostgresJobScheduleStore",
    "ScheduleNotFoundError",
    "ScheduleStatus",
    "compute_advanced_next_run_at",
    "compute_backoff_seconds",
    "generate_worker_id",
]
