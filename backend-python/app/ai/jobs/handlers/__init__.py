"""Register first-class job handlers (stubs until Phases 3–6)."""

from __future__ import annotations

from app.ai.jobs.models import BackgroundJob, JobResult
from app.ai.jobs.registry import JobHandlerRegistry

_FIRST_CLASS_JOB_TYPES: tuple[str, ...] = (
    "hitl_approval_expiry_sweep",
    "hitl_orphaned_snapshot_sweep",
    "workflow_run_retention_cleanup",
    "rag_document_indexing",
    "scheduled_evaluation_run",
)


async def _stub_handler(job: BackgroundJob) -> JobResult:
    return JobResult(summary=f"stub handler for {job.job_type}")


def register_all_handlers(registry: JobHandlerRegistry) -> None:
    """Wire all first-class handlers before worker/scheduler startup."""
    for job_type in _FIRST_CLASS_JOB_TYPES:
        registry.register(job_type, _stub_handler)
