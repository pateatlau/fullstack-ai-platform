"""Job worker poll/claim/dispatch loop (stable public API after Phase 1)."""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Sequence

from app.ai.jobs.exceptions import JobHandlerNotFoundError
from app.ai.jobs.models import BackgroundJob
from app.ai.jobs.queue import JobQueue, generate_worker_id
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.retry import NonRetryableJobError, compute_backoff_seconds
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


def _format_handler_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _log_gather_exceptions(
    results: Sequence[object],
    *,
    jobs: list[BackgroundJob] | None = None,
    tasks: list[asyncio.Task[None]] | None = None,
) -> None:
    for index, result in enumerate(results):
        if not isinstance(result, BaseException):
            continue
        log_kwargs: dict[str, object] = {
            "error": _format_handler_error(result),
        }
        if jobs is not None and index < len(jobs):
            job = jobs[index]
            log_kwargs["job_id"] = str(job.id)
            log_kwargs["job_type"] = job.job_type
            log_kwargs["attempt_count"] = job.attempt_count
        elif tasks is not None and index < len(tasks):
            task_name = tasks[index].get_name()
            if task_name:
                log_kwargs["job_id"] = task_name
        _logger.error(
            "Job dispatch task failed unexpectedly",
            exc_info=(type(result), result, result.__traceback__),
            **log_kwargs,
        )


class JobWorker:
    """Polls the queue, dispatches handlers, and records outcomes."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        registry: JobHandlerRegistry,
        settings: Settings,
        worker_id: str | None = None,
    ) -> None:
        self._queue = queue
        self._registry = registry
        self._settings = settings
        self._worker_id = worker_id or generate_worker_id()
        self._shutdown = asyncio.Event()
        self._in_flight: set[asyncio.Task[None]] = set()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def request_shutdown(self) -> None:
        """Stop accepting new claims; in-flight jobs may finish."""
        self._shutdown.set()

    async def run_forever(self) -> None:
        """Poll until :meth:`request_shutdown` and all in-flight jobs finish."""
        poll_interval = self._settings.background_jobs_worker_poll_interval_seconds
        while not self._shutdown.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=poll_interval)
            except TimeoutError:
                continue

        if self._in_flight:
            in_flight = list(self._in_flight)
            _log_gather_exceptions(
                await asyncio.gather(*in_flight, return_exceptions=True),
                tasks=in_flight,
            )

    async def poll_once(self) -> None:
        """Claim one batch and dispatch all claimed jobs concurrently."""
        if self._shutdown.is_set():
            return

        claimed = await self._queue.claim_due(
            worker_id=self._worker_id,
            batch_size=self._settings.background_jobs_worker_batch_size,
            lease_seconds=self._settings.background_jobs_claim_lease_seconds,
        )
        if not claimed:
            return

        tasks = [
            asyncio.create_task(self._dispatch(job), name=str(job.id))
            for job in claimed
        ]
        for task in tasks:
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        _log_gather_exceptions(results, jobs=claimed, tasks=tasks)

    async def _dispatch(self, job: BackgroundJob) -> None:
        try:
            handler = self._registry.resolve(job.job_type)
        except JobHandlerNotFoundError as exc:
            await self._dead_letter(job, error=str(exc))
            return

        timeout = self._settings.background_jobs_handler_timeout_seconds
        try:
            result = await asyncio.wait_for(handler(job), timeout=timeout)
        except NonRetryableJobError as exc:
            await self._dead_letter(job, error=str(exc))
        except TimeoutError:
            await self._handle_failure(
                job,
                error=f"TimeoutError: handler exceeded {timeout}s",
            )
        except Exception as exc:
            await self._handle_failure(job, error=_format_handler_error(exc))
        else:
            await self._queue.complete(
                job.id,
                result=result,
                expected_version=job.version,
            )

    async def _handle_failure(self, job: BackgroundJob, *, error: str) -> None:
        if job.attempt_count >= job.max_attempts:
            await self._dead_letter(job, error=error)
            return

        delay = compute_backoff_seconds(
            job.attempt_count - 1,
            base=self._settings.background_jobs_retry_base_delay_seconds,
            cap=self._settings.background_jobs_retry_max_delay_seconds,
        )
        retry_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            seconds=delay
        )
        await self._queue.fail(
            job.id,
            error=error,
            expected_version=job.version,
            retry_at=retry_at,
        )

    async def _dead_letter(self, job: BackgroundJob, *, error: str) -> None:
        await self._queue.fail(
            job.id,
            error=error,
            expected_version=job.version,
            dead_letter=True,
        )
        _logger.warning(
            "Job dead-lettered",
            job_id=str(job.id),
            job_type=job.job_type,
            error=error,
        )
