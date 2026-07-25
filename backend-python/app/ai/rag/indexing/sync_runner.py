"""In-process indexing job runner (Phase 9).

Implements :class:`~app.ai.interfaces.indexing_job.IndexingJob` without an
external broker. Callers register pending work (bytes + metadata) keyed by
``document_id``, then ``submit`` runs that work synchronously in-process.

TODO(epic-9): QueueIndexingRunner / workers / retries / durable job store.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.ai.rag.schemas import IndexingJobState, IndexingJobStatus
from app.core.logging import get_logger

_logger = get_logger(__name__)


class IndexingJobNotFoundError(LookupError):
    """Raised when ``get_status`` is called with an unknown job id."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Indexing job not found: {job_id}")
        self.job_id = job_id


@dataclass(frozen=True)
class PendingIndexingWork:
    """In-memory payload for a sync indexing run (not persisted)."""

    document_id: uuid.UUID
    user_id: uuid.UUID
    file_bytes: bytes
    filename: str
    mime_type: str | None


IndexingProcessor = Callable[[PendingIndexingWork], Awaitable[None]]


class SyncIndexingRunner:
    """Process-local :class:`~app.ai.interfaces.indexing_job.IndexingJob`.

    Status lives in an in-memory map. Pending upload bytes are held only until
    ``submit`` consumes them — no blob storage in this phase.
    """

    def __init__(self, *, processor: IndexingProcessor) -> None:
        self._processor = processor
        self._pending: dict[uuid.UUID, PendingIndexingWork] = {}
        self._jobs: dict[str, IndexingJobStatus] = {}

    def register_pending_work(self, work: PendingIndexingWork) -> None:
        """Stage work for a later ``submit`` on the same ``document_id``."""
        self._pending[work.document_id] = work

    async def submit(self, *, document_id: uuid.UUID, user_id: uuid.UUID) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = IndexingJobStatus(
            job_id=job_id,
            state=IndexingJobState.QUEUED,
        )

        work = self._pending.pop(document_id, None)
        if work is None or work.user_id != user_id:
            self._jobs[job_id] = IndexingJobStatus(
                job_id=job_id,
                state=IndexingJobState.FAILED,
                error_message="pending_work_missing",
            )
            _logger.error(
                "Indexing job failed before run",
                indexing_job_id=job_id,
                indexing_job_status=IndexingJobState.FAILED.value,
                document_id=str(document_id),
            )
            raise LookupError(f"No pending indexing work for document_id={document_id}")

        self._jobs[job_id] = IndexingJobStatus(
            job_id=job_id,
            state=IndexingJobState.RUNNING,
        )
        _logger.info(
            "Indexing job running",
            indexing_job_id=job_id,
            indexing_job_status=IndexingJobState.RUNNING.value,
            document_id=str(document_id),
        )

        try:
            await self._processor(work)
        except Exception as exc:
            self._jobs[job_id] = IndexingJobStatus(
                job_id=job_id,
                state=IndexingJobState.FAILED,
                error_message=type(exc).__name__,
            )
            _logger.error(
                "Indexing job failed",
                indexing_job_id=job_id,
                indexing_job_status=IndexingJobState.FAILED.value,
                document_id=str(document_id),
            )
            raise

        self._jobs[job_id] = IndexingJobStatus(
            job_id=job_id,
            state=IndexingJobState.SUCCEEDED,
        )
        _logger.info(
            "Indexing job succeeded",
            indexing_job_id=job_id,
            indexing_job_status=IndexingJobState.SUCCEEDED.value,
            document_id=str(document_id),
        )
        return job_id

    async def get_status(self, job_id: str) -> IndexingJobStatus:
        status = self._jobs.get(job_id)
        if status is None:
            raise IndexingJobNotFoundError(job_id)
        return status
