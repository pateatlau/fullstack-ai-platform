"""Queue-backed indexing runner (Epic 10 Phase 5)."""

from __future__ import annotations

import uuid

from app.ai.jobs.models import BackgroundJob, JobStatus
from app.ai.jobs.queue import JobQueue
from app.ai.rag.indexing.sync_runner import IndexingJobNotFoundError
from app.ai.rag.schemas import IndexingJobState, IndexingJobStatus

_PAYLOAD_VERSION = 1


def _indexing_error_message(last_error: str | None) -> str | None:
    if last_error is None:
        return None
    if ":" in last_error:
        return last_error.split(":", 1)[0].strip()
    return last_error


def to_indexing_job_status(job: BackgroundJob) -> IndexingJobStatus:
    """Map a background job row onto the IndexingJob status shape."""
    mapping = {
        JobStatus.QUEUED: IndexingJobState.QUEUED,
        JobStatus.RUNNING: IndexingJobState.RUNNING,
        JobStatus.SUCCEEDED: IndexingJobState.SUCCEEDED,
    }
    state = mapping.get(job.status)
    if state is not None:
        return IndexingJobStatus(job_id=str(job.id), state=state)

    return IndexingJobStatus(
        job_id=str(job.id),
        state=IndexingJobState.FAILED,
        error_message=_indexing_error_message(job.last_error),
    )


class QueueIndexingRunner:
    """IndexingJob implementation backed by the Background Jobs queue."""

    def __init__(self, *, queue: JobQueue) -> None:
        self._queue = queue

    async def submit(self, *, document_id: uuid.UUID, user_id: uuid.UUID) -> str:
        job = await self._queue.enqueue(
            job_type="rag_document_indexing",
            payload={
                "version": _PAYLOAD_VERSION,
                "document_id": str(document_id),
                "user_id": str(user_id),
            },
        )
        return str(job.id)

    async def get_status(self, job_id: str) -> IndexingJobStatus:
        job = await self._queue.get(uuid.UUID(job_id))
        if job is None:
            raise IndexingJobNotFoundError(job_id)
        return to_indexing_job_status(job)
